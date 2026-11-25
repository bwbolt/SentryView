"""One-shot stream probe used by 'Test connection'.

Grabs a single frame so the user sees proof the camera works, plus the codec
and resolution details that make troubleshooting possible.
"""
from __future__ import annotations

import re
import subprocess
import sys
import threading
from dataclasses import dataclass, field

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from .paths import find_ffmpeg

_RAW_DIMS_RE = re.compile(r"Video:\s*rawvideo.*?,\s*(\d{2,5})x(\d{2,5})")
_IN_VIDEO_RE = re.compile(
    r"Stream #0:\d+.*?: Video:\s*([\w]+).*?,\s*(\d{2,5})x(\d{2,5})")
_IN_AUDIO_RE = re.compile(r"Stream #0:\d+.*?: Audio:\s*([\w]+)")
_FPS_RE = re.compile(r",\s*([\d.]+)\s*fps")

PREVIEW_WIDTH = 480

_ERRNO_RE = re.compile(r"Error number (-?\d+) occurred")

# When the C library cannot describe an error, FFmpeg falls back to printing
# the raw errno - which is most of the time on Windows. The numbers differ per
# platform (Windows ETIMEDOUT is 138, Linux's is 110), so the wording has to
# cover both readings of a code where they collide.
_HINTS = {
    "timed out": (
        "The address is reachable or wrong, but nothing replied in time. Check "
        "that the camera's IP has not changed, that this computer is on the "
        "same network as the camera, and that any VPN is disconnected — a VPN "
        "routes local addresses away from your LAN. On Windows you can test "
        "the camera directly with:\n"
        "    Test-NetConnection <camera-ip> -Port 554"
    ),
    "no route": (
        "This computer cannot reach that address at all. It is usually a "
        "different subnet, a guest Wi-Fi network that isolates devices, or an "
        "active VPN."
    ),
    "unreachable": (
        "This computer cannot reach that address at all. It is usually a "
        "different subnet, a guest Wi-Fi network that isolates devices, or an "
        "active VPN."
    ),
    "refused": (
        "Something answered but nothing is listening on that port. RTSP is "
        "normally 554 — check the port, and that RTSP/ONVIF is enabled in the "
        "camera's own settings."
    ),
    "authentication": (
        "Some cameras need a separate RTSP user, distinct from the web login. "
        "Create one in the camera's settings if the main password is rejected."
    ),
    "path": (
        "The host is right but the part after it is wrong. Check the camera's "
        "manual for its exact RTSP path — Help ▸ Finding Your Camera's RTSP "
        "URL lists the common ones."
    ),
}

_ERRNO_MESSAGES = {
    138: "Timed out — the camera never answered",
    60: "Timed out — the camera never answered",
    110: "Timed out, or there is no route to the camera",
    107: "Connection refused — the camera said no on that port",
    111: "Connection refused — the camera said no on that port",
    61: "Connection refused — the camera said no on that port",
    118: "Network unreachable — the camera is not on this network",
    65: "No route to the camera — is it on this network?",
    116: "The network is down",
    108: "The camera closed the connection",
    104: "The camera closed the connection",
    54: "The camera closed the connection",
    106: "The connection was aborted",
}


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


@dataclass
class ProbeResult:
    ok: bool = False
    image: QImage | None = None
    codec: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    audio: str = ""
    error: str = ""
    hint: str = ""
    log: list = field(default_factory=list)

    def summary(self) -> str:
        if not self.ok:
            return self.error or "Could not connect"
        bits = []
        if self.width and self.height:
            bits.append(f"{self.width}×{self.height}")
        if self.codec:
            bits.append(self.codec.upper())
        if self.fps:
            bits.append(f"{self.fps:g} fps")
        bits.append(f"audio: {self.audio.upper()}" if self.audio else "no audio")
        return "Connected · " + " · ".join(bits)


def grab_frame(url: str, transport: str = "tcp", timeout: int = 25) -> ProbeResult:
    """Connect, pull one frame, and report what was found."""
    result = ProbeResult()
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        result.error = "ffmpeg binary not found"
        return result
    if not url.strip():
        result.error = "Enter a stream URL first"
        return result

    cmd = [ffmpeg, "-hide_banner", "-loglevel", "info", "-nostdin"]
    if url.lower().startswith("rtsp"):
        cmd += ["-rtsp_transport", transport or "tcp",
                "-timeout", str(max(3, timeout - 5) * 1_000_000)]
    cmd += [
        "-i", url,
        "-map", "0:v:0", "-an", "-sn",
        "-frames:v", "1",
        "-vf", f"scale={PREVIEW_WIDTH}:-2",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL,
                                creationflags=_creation_flags())
    except OSError as exc:
        result.error = f"Could not start ffmpeg: {exc}"
        return result

    # Drain both pipes from their own threads. Using communicate() here would
    # race whichever thread also reads stdout, and the frame would arrive
    # truncated or not at all.
    chunks: dict = {"out": [], "err": []}

    def drain(stream, key: str) -> None:
        try:
            while True:
                block = stream.read(65536)
                if not block:
                    break
                chunks[key].append(block)
        except (OSError, ValueError):
            pass

    readers = [
        threading.Thread(target=drain, args=(proc.stdout, "out"), daemon=True),
        threading.Thread(target=drain, args=(proc.stderr, "err"), daemon=True),
    ]
    for reader in readers:
        reader.start()

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        result.error = f"Timed out after {timeout}s — no response from the camera"
    for reader in readers:
        reader.join(timeout=4)

    text = b"".join(chunks["err"]).decode("utf-8", "replace")
    result.log = [line for line in text.splitlines() if line.strip()][-25:]

    m = _IN_VIDEO_RE.search(text)
    if m:
        result.codec = m.group(1)
        result.width, result.height = int(m.group(2)), int(m.group(3))
    m = _IN_AUDIO_RE.search(text)
    if m:
        result.audio = m.group(1)
    m = _FPS_RE.search(text)
    if m:
        try:
            result.fps = float(m.group(1))
        except ValueError:
            pass

    raw = b"".join(chunks["out"])
    dims = _RAW_DIMS_RE.search(text)
    if raw and dims:
        width, height = int(dims.group(1)), int(dims.group(2))
        needed = width * height * 3
        if len(raw) >= needed:
            result.image = QImage(bytes(raw[:needed]), width, height, width * 3,
                                  QImage.Format.Format_RGB888).copy()
            result.ok = True

    if not result.ok and not result.error:
        result.error = friendly_error(text)
    if not result.ok:
        result.hint = hint_for(result.error)
    return result


def hint_for(message: str) -> str:
    """A next step to try, for the failures people actually hit."""
    low = (message or "").lower()
    for needle, hint in _HINTS.items():
        if needle in low:
            return hint
    return ""


def friendly_error(text: str) -> str:
    """Translate ffmpeg's output into something actionable."""
    low = text.lower()

    # Numeric errnos first: they are precise, and on Windows they are all we get.
    for match in _ERRNO_RE.finditer(text):
        try:
            code = abs(int(match.group(1)))
        except ValueError:
            continue
        if code in _ERRNO_MESSAGES:
            return _ERRNO_MESSAGES[code]

    rules = [
        ("401", "Authentication failed — check the username and password"),
        ("unauthorized", "Authentication failed — check the username and password"),
        ("connection refused", "Connection refused — check the IP address and port"),
        ("no route to host", "No route to host — is the camera on this network?"),
        ("timed out", "Timed out — check the address, or try UDP transport"),
        ("timeout", "Timed out — check the address, or try UDP transport"),
        ("404", "Stream path not found — check the path after the host"),
        ("not found", "Stream path not found — check the path after the host"),
        ("invalid data found", "Connected, but the stream could not be decoded"),
        ("protocol not found", "Unsupported URL scheme"),
        ("server returned 5", "The camera reported a server error"),
    ]
    for needle, message in rules:
        if needle in low:
            return message
    for line in reversed(text.splitlines()):
        if any(k in line.lower() for k in ("error", "failed", "could not")):
            return re.sub(r"^\[[^\]]+\]\s*", "", line.strip())[:200]
    return "Could not read video from this URL"


class ProbeWorker(QThread):
    """Runs :func:`grab_frame` without blocking the UI."""

    done = Signal(object)

    def __init__(self, url: str, transport: str, parent=None) -> None:
        super().__init__(parent)
        self.url = url
        self.transport = transport

    def run(self) -> None:
        self.done.emit(grab_frame(self.url, self.transport))
