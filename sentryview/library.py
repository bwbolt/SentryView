"""Read-only index of what has been recorded.

The folder layout is the index — no database to drift out of sync:

    <root>/<camera folder>/<YYYY-MM-DD>/<HH-MM-SS>.mp4
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

DAY_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
CLIP_RE = re.compile(r"^(\d{2})-(\d{2})-(\d{2})\.mp4$")


@dataclass
class Clip:
    path: Path
    camera_folder: str
    day: dt.date
    start: dt.datetime
    size: int
    duration: float          # derived from mtime; the player reports exact length

    @property
    def end(self) -> dt.datetime:
        return self.start + dt.timedelta(seconds=self.duration)

    def label(self) -> str:
        return self.start.strftime("%H:%M:%S")


def parse_day(name: str) -> dt.date | None:
    m = DAY_RE.match(name)
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def parse_clip_time(name: str) -> dt.time | None:
    m = CLIP_RE.match(name)
    if not m:
        return None
    try:
        return dt.time(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


class Library:
    """Scans the recording root on demand."""

    def __init__(self, root: str | Path | None) -> None:
        self.root = Path(root).expanduser() if root else None

    def available(self) -> bool:
        return bool(self.root and self.root.is_dir())

    # ------------------------------------------------------------------ listing
    def camera_folders(self) -> list[str]:
        if not self.available():
            return []
        try:
            return sorted(
                d.name for d in self.root.iterdir()
                if d.is_dir() and not d.name.startswith(".")
            )
        except OSError:
            return []

    def days(self, camera_folder: str) -> list[dt.date]:
        if not self.available():
            return []
        base = self.root / camera_folder
        if not base.is_dir():
            return []
        out = []
        try:
            for child in base.iterdir():
                day = parse_day(child.name)
                if day and child.is_dir():
                    out.append(day)
        except OSError:
            return []
        return sorted(out, reverse=True)

    def clips(self, camera_folder: str, day: dt.date) -> list[Clip]:
        """Clips for one camera-day, oldest first."""
        if not self.available():
            return []
        folder = self.root / camera_folder / day.isoformat()
        if not folder.is_dir():
            return []

        found = []
        try:
            entries = list(folder.iterdir())
        except OSError:
            return []

        for entry in entries:
            clip_time = parse_clip_time(entry.name)
            if clip_time is None:
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            if stat.st_size == 0:
                continue
            start = dt.datetime.combine(day, clip_time)
            # mtime lands at the final write, so it approximates the clip end.
            duration = max(0.0, stat.st_mtime - start.timestamp())
            if duration > 24 * 3600:
                duration = 0.0
            found.append(Clip(entry, camera_folder, day, start, stat.st_size, duration))

        found.sort(key=lambda c: c.start)

        # A clip covers the wall-clock span up to the next one, which makes the
        # day read as a continuous timeline. But recording can stop and restart,
        # and that idle gap is not footage - so the span is only trusted when it
        # roughly agrees with the clip's own mtime estimate.
        for index in range(len(found) - 1):
            current = found[index]
            gap = (found[index + 1].start - current.start).total_seconds()
            if gap <= 0:
                continue
            estimate = current.duration
            if estimate <= 0 or gap <= estimate + 30:
                current.duration = gap
        return found

    def day_summary(self, camera_folder: str, day: dt.date) -> tuple[int, int]:
        """(clip count, total bytes) without building Clip objects."""
        folder = self.root / camera_folder / day.isoformat() if self.available() else None
        if not folder or not folder.is_dir():
            return 0, 0
        count = total = 0
        try:
            for entry in folder.iterdir():
                if parse_clip_time(entry.name):
                    try:
                        total += entry.stat().st_size
                    except OSError:
                        continue
                    count += 1
        except OSError:
            pass
        return count, total

    # -------------------------------------------------------------------- stats
    def all_clips(self) -> list[Clip]:
        """Every clip under the root, oldest first."""
        out: list[Clip] = []
        for folder in self.camera_folders():
            for day in self.days(folder):
                out.extend(self.clips(folder, day))
        out.sort(key=lambda c: c.start)
        return out

    def total_bytes(self) -> int:
        if not self.available():
            return 0
        total = 0
        for folder in self.camera_folders():
            for day in self.days(folder):
                total += self.day_summary(folder, day)[1]
        return total

    def oldest_day(self) -> dt.date | None:
        days = [d for f in self.camera_folders() for d in self.days(f)]
        return min(days) if days else None
