"""Thumbnail extraction, cached on disk.

Thumbnails are keyed by path, size and mtime, so a clip still being written
gets a fresh thumbnail once it is finished.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

from .paths import find_ffmpeg, thumb_cache_dir

THUMB_WIDTH = 320


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def cache_key(clip: Path) -> str:
    try:
        stat = clip.stat()
        seed = f"{clip}|{stat.st_size}|{int(stat.st_mtime)}"
    except OSError:
        seed = str(clip)
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()


def thumbnail_for(clip: Path, offset: float = 2.0,
                  width: int = THUMB_WIDTH) -> Path | None:
    """Return a cached JPEG for ``clip``, extracting it if needed."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg or not clip.exists():
        return None

    target = thumb_cache_dir() / f"{cache_key(clip)}_{width}.jpg"
    if target.exists() and target.stat().st_size > 0:
        return target

    for seek in (offset, 0.0):
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
            "-ss", f"{seek:.2f}", "-i", str(clip),
            "-frames:v", "1",
            "-vf", f"scale={width}:-2",
            "-q:v", "4", "-y", str(target),
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           stdin=subprocess.DEVNULL, timeout=25,
                           creationflags=_creation_flags())
        except (OSError, subprocess.SubprocessError):
            return None
        if target.exists() and target.stat().st_size > 0:
            return target
    return None


def purge_orphans(valid_keys: set) -> int:
    """Drop cached thumbnails whose clips are gone."""
    removed = 0
    try:
        entries = list(thumb_cache_dir().iterdir())
    except OSError:
        return 0
    for entry in entries:
        if entry.suffix != ".jpg":
            continue
        if entry.stem.split("_")[0] not in valid_keys:
            try:
                entry.unlink()
                removed += 1
            except OSError:
                pass
    return removed
