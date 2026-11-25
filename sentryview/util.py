"""Small formatting helpers shared by the UI."""
from __future__ import annotations

import datetime as dt


_CREDENTIAL_RE = __import__("re").compile(
    r"([a-zA-Z][a-zA-Z0-9+.\-]*://)([^:/@\s]+):([^@/\s]+)@")


def redact_credentials(text: str) -> str:
    """Mask passwords in anything that might be shown or logged.

    FFmpeg echoes the full stream URL, credentials included, and we surface
    its output in the Test-connection details pane and on camera tiles.
    """
    if not text:
        return text
    return _CREDENTIAL_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}:*****@", text)


def human_size(num_bytes: float) -> str:
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < step or unit == "TB":
            if unit == "B":
                return f"{num_bytes:.0f} B"
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= step
    return f"{num_bytes:.1f} TB"


def human_duration(seconds: float) -> str:
    if seconds is None or seconds < 0:
        return "--:--"
    seconds = int(round(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def friendly_day(day: dt.date) -> str:
    today = dt.date.today()
    if day == today:
        return f"Today · {day.strftime('%b %d')}"
    if day == today - dt.timedelta(days=1):
        return f"Yesterday · {day.strftime('%b %d')}"
    return day.strftime("%a %b %d, %Y")


def reveal_in_file_manager(path) -> None:
    """Show a file (or folder) in Finder / Explorer / the desktop's file manager."""
    import subprocess
    import sys
    from pathlib import Path

    target = Path(path)
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(target)])
        elif sys.platform == "win32":
            if target.is_dir():
                subprocess.Popen(["explorer", str(target)])
            else:
                subprocess.Popen(["explorer", f"/select,{target}"])
        else:
            subprocess.Popen(["xdg-open", str(target if target.is_dir()
                                              else target.parent)])
    except OSError:
        pass


def open_path(path) -> None:
    """Open a file or folder with the system default handler."""
    import subprocess
    import sys
    from pathlib import Path

    target = Path(path)
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        elif sys.platform == "win32":
            import os
            os.startfile(str(target))       # noqa: S606
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except (OSError, AttributeError):
        pass
