"""Clip playback and export, driven by ffmpeg.

Decoding through ffmpeg rather than a platform media framework keeps playback
behaviour identical on macOS and Windows, and guarantees any clip we recorded
is a clip we can play back.
"""
from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QImage

from .paths import find_ffmpeg

IDLE = "idle"
LOADING = "loading"
PLAYING = "playing"
PAUSED = "paused"
ENDED = "ended"
PLAY_ERROR = "error"

DECODE_WIDTH = 960
SPEEDS = [0.25, 0.5, 1.0, 2.0, 4.0, 8.0]

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_FPS_RE = re.compile(r",\s*([\d.]+)\s*fps")
_DIMS_RE = re.compile(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})")
_RAW_DIMS_RE = re.compile(r"Video:\s*rawvideo.*?,\s*(\d{2,5})x(\d{2,5})")


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


class ClipInfo:
    __slots__ = ("duration", "fps", "width", "height")

    def __init__(self, duration: float = 0.0, fps: float = 15.0,
                 width: int = 0, height: int = 0) -> None:
        self.duration = duration
        self.fps = fps
        self.width = width
        self.height = height


def probe_clip(path: Path) -> ClipInfo:
    """Read duration, frame rate and dimensions out of ffmpeg's banner."""
    ffmpeg = find_ffmpeg()
    info = ClipInfo()
    if not ffmpeg or not Path(path).exists():
        return info
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, timeout=30,
            creationflags=_creation_flags(),
        )
    except (OSError, subprocess.SubprocessError):
        return info

    text = proc.stderr.decode("utf-8", "replace")
    m = _DURATION_RE.search(text)
    if m:
        info.duration = (int(m.group(1)) * 3600 + int(m.group(2)) * 60
                         + float(m.group(3)))
    m = _FPS_RE.search(text)
    if m:
        try:
            value = float(m.group(1))
            if 0.5 <= value <= 240:
                info.fps = value
        except ValueError:
            pass
    m = _DIMS_RE.search(text)
    if m:
        info.width, info.height = int(m.group(1)), int(m.group(2))
    return info


def export_clip(src: Path, dst: Path, start: float | None = None,
                end: float | None = None) -> tuple[bool, str]:
    """Copy a clip, optionally trimming, without re-encoding."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return False, "ffmpeg not found"
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if start:
        cmd += ["-ss", f"{max(0.0, start):.3f}"]
    cmd += ["-i", str(src)]
    if end is not None and start is not None and end > start:
        cmd += ["-t", f"{end - start:.3f}"]
    elif end is not None and not start:
        cmd += ["-t", f"{end:.3f}"]
    cmd += ["-c", "copy", "-movflags", "+faststart", str(dst)]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              stdin=subprocess.DEVNULL, timeout=600,
                              creationflags=_creation_flags())
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if proc.returncode != 0:
        return False, proc.stderr.decode("utf-8", "replace")[-300:] or "export failed"
    return True, str(dst)


class ClipPlayer(QThread):
    """Decodes one clip at a time, pacing frames to wall clock."""

    frameReady = Signal(QImage)
    positionChanged = Signal(float)
    durationChanged = Signal(float)
    stateChanged = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()

        self._path: Path | None = None
        self._info = ClipInfo()
        self._offset = 0.0              # where the current ffmpeg process started
        self._emitted = 0               # frames emitted since that start
        self._speed = 1.0
        self._paused = True
        self._pending_seek: float | None = None
        self._pending_load: Path | None = None
        self._step = 0
        self._proc: subprocess.Popen | None = None
        self.state = IDLE

    # ------------------------------------------------------------------ controls
    def load(self, path: Path) -> None:
        with self._lock:
            self._pending_load = Path(path)
            self._pending_seek = 0.0
            self._paused = True
            self._step = 1          # show the opening frame straight away
        self._wake.set()

    def play(self) -> None:
        with self._lock:
            if self.state == ENDED:
                self._pending_seek = 0.0
            self._paused = False
        self._wake.set()

    def pause(self) -> None:
        with self._lock:
            self._paused = True
        self._wake.set()

    def toggle(self) -> None:
        if self.state == PLAYING:
            self.pause()
        else:
            self.play()

    def seek(self, seconds: float) -> None:
        with self._lock:
            target = max(0.0, seconds)
            if self._info.duration:
                target = min(target, max(0.0, self._info.duration - 0.05))
            self._pending_seek = target
            if self._paused:
                self._step = max(self._step, 1)
        self._wake.set()

    def step_frames(self, count: int = 1) -> None:
        with self._lock:
            self._paused = True
            self._step += max(1, count)
        self._wake.set()

    def set_speed(self, speed: float) -> None:
        with self._lock:
            self._speed = max(0.1, min(16.0, float(speed)))
        self._wake.set()

    @property
    def speed(self) -> float:
        return self._speed

    def position(self) -> float:
        fps = self._info.fps or 15.0
        return self._offset + self._emitted / fps

    def duration(self) -> float:
        return self._info.duration

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        self._kill_proc()
        self.wait(6000)

    # -------------------------------------------------------------------- engine
    def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.stateChanged.emit(state)

    def _kill_proc(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=3)
            except Exception:
                pass

    def _spawn(self, offset: float):
        ffmpeg = find_ffmpeg()
        if not ffmpeg or not self._path:
            return None, None
        fps = self._info.fps or 15.0
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "info", "-nostdin",
            "-ss", f"{max(0.0, offset):.3f}",
            "-i", str(self._path),
            "-map", "0:v:0", "-an", "-sn",
            "-vf", f"fps={fps:.4f},scale={DECODE_WIDTH}:-2",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    stdin=subprocess.DEVNULL, bufsize=0,
                                    creationflags=_creation_flags())
        except OSError:
            return None, None

        dims: list = []
        ready = threading.Event()

        def watch():
            if proc.stderr is None:
                ready.set()
                return
            for raw in iter(proc.stderr.readline, b""):
                if not dims:
                    m = _RAW_DIMS_RE.search(raw.decode("utf-8", "replace"))
                    if m:
                        dims.extend((int(m.group(1)), int(m.group(2))))
                        ready.set()
            ready.set()

        threading.Thread(target=watch, daemon=True).start()
        ready.wait(20)
        if len(dims) != 2:
            try:
                proc.kill()
            except Exception:
                pass
            return None, None
        return proc, (dims[0], dims[1])

    def run(self) -> None:
        dims: tuple | None = None
        buf = bytearray()
        next_due = 0.0

        while not self._stop.is_set():
            with self._lock:
                pending_load = self._pending_load
                self._pending_load = None
                pending_seek = self._pending_seek
                self._pending_seek = None
                paused = self._paused
                step = self._step
                speed = self._speed

            if pending_load is not None:
                self._kill_proc()
                self._path = pending_load
                self._set_state(LOADING)
                self._info = probe_clip(self._path)
                self.durationChanged.emit(self._info.duration)
                dims = None
                if self._info.duration <= 0 and not self._info.width:
                    self._set_state(PLAY_ERROR)
                    continue

            if pending_seek is not None and self._path:
                self._kill_proc()
                dims = None
                self._offset = pending_seek
                self._emitted = 0
                self.positionChanged.emit(self._offset)

            if self._path is None:
                self._wake.wait(0.2)
                self._wake.clear()
                continue

            with self._lock:
                proc = self._proc
            if proc is None:
                proc, dims = self._spawn(self._offset)
                if proc is None or dims is None:
                    self._set_state(PLAY_ERROR)
                    self._wake.wait(0.5)
                    self._wake.clear()
                    continue
                with self._lock:
                    self._proc = proc
                buf = bytearray(dims[0] * dims[1] * 3)
                next_due = time.monotonic()

            if paused and step <= 0:
                self._set_state(PAUSED if self.state != LOADING else PAUSED)
                self._wake.wait(0.2)
                self._wake.clear()
                continue

            # Pace to wall clock unless we are stepping or scrubbing.
            if not paused:
                self._set_state(PLAYING)
                interval = 1.0 / max(0.1, (self._info.fps or 15.0) * speed)
                now = time.monotonic()
                if next_due > now:
                    if self._wake.wait(min(next_due - now, 0.25)):
                        self._wake.clear()
                        continue
                next_due = max(now, next_due) + interval

            view = memoryview(buf)
            got = 0
            ended = False
            while got < len(buf):
                try:
                    n = proc.stdout.readinto(view[got:])
                except OSError:
                    n = 0
                if not n:
                    ended = True
                    break
                got += n

            if ended:
                self._kill_proc()
                dims = None
                if self._info.duration:
                    self.positionChanged.emit(self._info.duration)
                self._set_state(ENDED)
                with self._lock:
                    self._paused = True
                    self._step = 0
                self._wake.wait(0.2)
                self._wake.clear()
                continue

            image = QImage(bytes(buf), dims[0], dims[1], dims[0] * 3,
                           QImage.Format.Format_RGB888)
            self._emitted += 1
            self.frameReady.emit(image)
            self.positionChanged.emit(self.position())

            if step > 0:
                with self._lock:
                    self._step = max(0, self._step - 1)
                    if self._step == 0:
                        self._paused = True
                self._set_state(PAUSED)

        self._kill_proc()
