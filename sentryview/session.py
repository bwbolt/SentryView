"""One ffmpeg process per camera, feeding both the live preview and disk.

A single RTSP connection is multiplexed into two ffmpeg outputs: a segmented
MP4 recording (stream-copied, so near-zero CPU) and a downscaled rawvideo
stream piped back to us for on-screen preview. Cheap cameras often refuse more
than one or two concurrent clients, so sharing the connection matters.
"""
from __future__ import annotations

import datetime as dt
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QImage

from .config import Camera, Storage
from .paths import find_ffmpeg

# Connection/record states surfaced to the UI.
CONNECTING = "connecting"
LIVE = "live"
RECONNECTING = "reconnecting"
ERROR = "error"
STOPPED = "stopped"
DISABLED = "disabled"

_RAW_DIMS_RE = re.compile(r"Video:\s*rawvideo.*?,\s*(\d{2,5})x(\d{2,5})")
_SEG_OPEN_RE = re.compile(r"Opening '([^']+\.mp4)' for writing")
_FATAL_HINTS = (
    "could not write header",
    "not currently supported in container",
    "incorrect codec parameters",
    "invalid data found when processing input",
)
_BACKOFF = [1, 2, 4, 8, 15, 30]


def _creation_flags() -> int:
    """Keep ffmpeg from flashing a console window on Windows."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _read_exact(stream, buf: memoryview) -> bool:
    """Fill ``buf`` completely; False if the stream ended first."""
    got = 0
    size = len(buf)
    while got < size:
        n = stream.readinto(buf[got:])
        if not n:
            return False
        got += n
    return True


class CameraSession(QThread):
    """Runs (and keeps alive) one camera's ffmpeg pipeline."""

    frameReady = Signal(str, QImage)        # camera_id, frame
    stateChanged = Signal(str, str, str)    # camera_id, state, detail
    clipStarted = Signal(str, str)          # camera_id, clip path

    def __init__(self, camera: Camera, storage: Storage, live_fps: int,
                 tile_width: int, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.camera = camera
        self.storage = storage
        self.live_fps = max(1, min(30, live_fps))
        self.tile_width = max(160, min(1920, tile_width))

        self._stop = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

        self._dims: tuple[int, int] | None = None
        self._dims_ready = threading.Event()
        self._stderr_tail: list[str] = []
        self._fatal_hint = False
        self._force_reencode = False

        self.current_clip: str | None = None
        self.frames_seen = 0
        self.preview_fps = 0.0
        self.last_error = ""
        self.state = CONNECTING

    # ------------------------------------------------------------------ command
    def _recording_enabled(self) -> bool:
        return bool(
            self.storage.enabled
            and self.storage.root
            and self.camera.record
        )

    def _camera_dir(self) -> Path:
        return Path(self.storage.root).expanduser() / self.camera.folder_name()

    def ensure_day_dirs(self) -> None:
        """Pre-create today's and tomorrow's folders.

        ffmpeg's segment muxer will not create directories itself, so a clip
        rolling over midnight would otherwise fail.
        """
        if not self._recording_enabled():
            return
        today = dt.date.today()
        for day in (today, today + dt.timedelta(days=1)):
            try:
                (self._camera_dir() / day.isoformat()).mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self.last_error = f"Cannot write to recording folder: {exc}"

    def build_command(self) -> list[str]:
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            raise RuntimeError("ffmpeg binary not found")

        cmd = [ffmpeg, "-hide_banner", "-loglevel", "info", "-nostdin"]

        url = self.camera.effective_url()
        if url.lower().startswith("rtsp"):
            cmd += ["-rtsp_transport", self.camera.transport or "tcp",
                    "-timeout", "10000000"]       # microseconds; socket I/O timeout
        if self.camera.fix_timestamps:
            cmd += ["-use_wallclock_as_timestamps", "1"]
        cmd += ["-i", url]

        if self._recording_enabled():
            pattern = str(self._camera_dir() / "%Y-%m-%d" / "%H-%M-%S.mp4")
            seg = max(1, int(round(float(self.storage.segment_minutes) * 60)))
            cmd += ["-map", "0:v:0"]
            if self.storage.record_audio:
                cmd += ["-map", "0:a?", "-c:a", "aac", "-b:a", "96k"]
            else:
                cmd += ["-an"]
            if self._force_reencode:
                cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                        "-pix_fmt", "yuv420p"]
            else:
                cmd += ["-c:v", "copy"]
            cmd += [
                "-f", "segment",
                "-segment_time", str(seg),
                "-segment_atclocktime", "1",     # clips start on clean clock boundaries
                "-reset_timestamps", "1",
                "-strftime", "1",
                "-segment_format", "mp4",
                # fragmented mp4 so a clip stays playable if we are killed mid-write
                "-segment_format_options",
                "movflags=+frag_keyframe+empty_moov+default_base_moof",
                pattern,
            ]

        # Preview output: downscaled rawvideo on stdout.
        cmd += [
            "-map", "0:v:0", "-an", "-sn",
            "-vf", f"fps={self.live_fps},scale={self.tile_width}:-2",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ]
        return cmd

    # -------------------------------------------------------------------- state
    def _set_state(self, state: str, detail: str = "") -> None:
        self.state = state
        if detail:
            self.last_error = detail
        self.stateChanged.emit(self.camera.id, state, detail)

    # ------------------------------------------------------------------- stderr
    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        """Watch ffmpeg's log for frame dimensions, clip rollovers and errors."""
        stream = proc.stderr
        if stream is None:
            return
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if not line:
                continue

            self._stderr_tail.append(line)
            if len(self._stderr_tail) > 40:
                self._stderr_tail.pop(0)

            if self._dims is None:
                m = _RAW_DIMS_RE.search(line)
                if m:
                    self._dims = (int(m.group(1)), int(m.group(2)))
                    self._dims_ready.set()

            m = _SEG_OPEN_RE.search(line)
            if m:
                self.current_clip = m.group(1)
                self.clipStarted.emit(self.camera.id, m.group(1))

            low = line.lower()
            if any(h in low for h in _FATAL_HINTS):
                self._fatal_hint = True
        self._dims_ready.set()      # unblock the reader if we never got dimensions

    def _error_summary(self) -> str:
        """Pick the most useful line out of ffmpeg's recent output."""
        if self._stderr_tail:
            # Same translation the Test-connection dialog uses, so a tile and
            # the dialog never disagree about what went wrong.
            from .probe import friendly_error

            summary = friendly_error("\n".join(self._stderr_tail))
            if summary:
                return summary[:200]
        return "Stream ended"

    # --------------------------------------------------------------------- loop
    def run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            self._dims = None
            self._dims_ready.clear()
            self._stderr_tail = []
            self._fatal_hint = False
            self.current_clip = None

            self.ensure_day_dirs()
            self._set_state(CONNECTING if attempt == 0 else RECONNECTING)

            started = time.monotonic()
            delivered = self._run_once()
            ran_for = time.monotonic() - started

            if self._stop.is_set():
                break

            # A stream-copy recording fails instantly when the camera's codec
            # cannot live in MP4; re-encoding is the fallback.
            if (not delivered and ran_for < 6 and self._recording_enabled()
                    and not self._force_reencode
                    and (self._fatal_hint or attempt >= 1)):
                self._force_reencode = True
                self._set_state(RECONNECTING, "Stream copy failed; re-encoding instead")
                attempt = 0
                continue

            if delivered:
                attempt = 0         # the stream worked, so start backoff over
            delay = _BACKOFF[min(attempt, len(_BACKOFF) - 1)]
            attempt += 1
            self._set_state(RECONNECTING, self._error_summary())
            if self._stop.wait(delay):
                break

        self._set_state(STOPPED)

    def _run_once(self) -> bool:
        """One ffmpeg lifetime. Returns True if any frame was delivered."""
        try:
            cmd = self.build_command()
        except RuntimeError as exc:
            self._set_state(ERROR, str(exc))
            self._stop.wait(5)
            return False

        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL, bufsize=0,
                creationflags=_creation_flags(),
            )
        except OSError as exc:
            self._set_state(ERROR, f"Could not start ffmpeg: {exc}")
            self._stop.wait(5)
            return False

        with self._lock:
            self._proc = proc

        watcher = threading.Thread(target=self._drain_stderr, args=(proc,), daemon=True)
        watcher.start()

        # A dedicated ticker keeps day folders present across midnight.
        keeper = threading.Thread(target=self._day_dir_keeper, daemon=True)
        keeper.start()

        delivered = False
        try:
            self._dims_ready.wait(45)
            if self._dims is None:
                raise OSError("no video stream")

            width, height = self._dims
            frame_bytes = width * height * 3
            buf = bytearray(frame_bytes)
            view = memoryview(buf)
            window_start = time.monotonic()
            window_frames = 0

            while not self._stop.is_set():
                if not _read_exact(proc.stdout, view):
                    break
                image = QImage(bytes(buf), width, height, width * 3,
                               QImage.Format.Format_RGB888)
                self.frames_seen += 1
                window_frames += 1
                if not delivered:
                    delivered = True
                    self._set_state(LIVE)
                self.frameReady.emit(self.camera.id, image)

                elapsed = time.monotonic() - window_start
                if elapsed >= 2.0:
                    self.preview_fps = window_frames / elapsed
                    window_start, window_frames = time.monotonic(), 0
        except OSError:
            pass
        finally:
            self._terminate(proc)
            watcher.join(timeout=2)
            with self._lock:
                self._proc = None
        return delivered

    def _day_dir_keeper(self) -> None:
        while not self._stop.is_set():
            if self._stop.wait(60):
                return
            with self._lock:
                alive = self._proc is not None
            if not alive:
                return
            self.ensure_day_dirs()

    @staticmethod
    def _terminate(proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        try:
            proc.terminate()            # lets ffmpeg finalise the current clip
            proc.wait(timeout=6)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=3)
            except Exception:
                pass

    # -------------------------------------------------------------------- public
    def request_stop(self) -> None:
        """Signal the pipeline to wind down without blocking the caller.

        ffmpeg is asked to terminate rather than killed, so it finalises the
        clip it is writing. Shutting many cameras down in parallel keeps quit
        times flat instead of adding up.
        """
        self._stop.set()
        self._dims_ready.set()
        with self._lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def stop(self, wait_ms: int = 9000) -> None:
        self.request_stop()
        with self._lock:
            proc = self._proc
        if proc is not None:
            self._terminate(proc)
        self.wait(wait_ms)

    def is_recording(self) -> bool:
        return self._recording_enabled() and self.state == LIVE
