"""Filesystem locations and bundled-binary resolution.

Everything the app writes lives under :func:`app_data_dir`, except recordings,
which go wherever the user pointed them.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from . import APP_NAME


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


def resource_dir() -> Path:
    """Directory holding bundled read-only resources."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def app_data_dir() -> Path:
    """Per-user writable directory for config, logs and thumbnail cache.

    ``SENTRYVIEW_DATA_DIR`` overrides the location, which makes portable
    installs (and tests) possible.
    """
    override = os.environ.get("SENTRYVIEW_DATA_DIR")
    if override:
        d = Path(override).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        return d
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    d = base / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return app_data_dir() / "config.json"


def log_path() -> Path:
    return app_data_dir() / "sentryview.log"


def thumb_cache_dir() -> Path:
    d = app_data_dir() / "thumbnails"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_recordings_root() -> Path:
    """Sensible default the storage wizard pre-fills."""
    if sys.platform == "darwin":
        parent = Path.home() / "Movies"
    elif os.name == "nt":
        parent = Path.home() / "Videos"
    else:
        parent = Path.home() / "Videos"
    if not parent.exists():
        parent = Path.home()
    return parent / APP_NAME


def _exe(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def find_ffmpeg() -> str | None:
    """Locate the ffmpeg binary.

    Order: binary shipped inside the bundle, then the ``imageio-ffmpeg``
    wheel's copy (development), then anything on PATH.
    """
    bundled = resource_dir() / "bin" / _exe("ffmpeg")
    if bundled.exists():
        return str(bundled)

    try:
        import imageio_ffmpeg

        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and Path(path).exists():
            return path
    except Exception:
        pass

    return shutil.which("ffmpeg")
