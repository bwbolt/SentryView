"""Storage policy enforcement: delete old clips so the disk never fills.

This is the one component that destroys user data, so every deletion is
gated on the path matching the exact recording layout. Nothing outside
``<root>/<camera>/<YYYY-MM-DD>/<HH-MM-SS>.mp4`` is ever touched.
"""
from __future__ import annotations

import datetime as dt
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .library import Library, parse_clip_time, parse_day


@dataclass
class SweepReport:
    removed_files: int = 0
    freed_bytes: int = 0
    removed_days: int = 0
    reasons: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def describe(self) -> str:
        if not self.removed_files and not self.removed_days:
            return "Nothing to remove"
        from .util import human_size
        return (f"Removed {self.removed_files} clip(s), "
                f"freed {human_size(self.freed_bytes)}")


def _is_recording_clip(path: Path, root: Path) -> bool:
    """True only for a file at the exact depth and shape we write."""
    try:
        rel = path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    parts = rel.parts
    if len(parts) != 3:                      # camera / day / clip
        return False
    if path.is_symlink() or not path.is_file():
        return False
    return parse_day(parts[1]) is not None and parse_clip_time(parts[2]) is not None


# Clips keep growing between sweeps, and the one being written can never be
# deleted, so sweeping to exactly the cap guarantees sitting just over it.
# Aiming slightly under absorbs the growth instead.
SIZE_HEADROOM = 0.95


class RetentionManager:
    """Applies the age limit first, then the size cap."""

    def __init__(self, root: str | Path | None, retention_days: int = 0,
                 max_total_gb: float = 0.0) -> None:
        self.root = Path(root).expanduser() if root else None
        self.retention_days = max(0, int(retention_days or 0))
        self.max_total_gb = max(0.0, float(max_total_gb or 0.0))

    def sweep(self, protect: set | None = None) -> SweepReport:
        report = SweepReport()
        protect = {str(Path(p).resolve()) for p in (protect or set()) if p}

        if not self.root or not self.root.is_dir():
            return report
        if not self.retention_days and not self.max_total_gb:
            return report

        library = Library(self.root)

        if self.retention_days:
            self._sweep_by_age(library, protect, report)
        if self.max_total_gb:
            self._sweep_by_size(library, protect, report)

        self._prune_empty_days(report)
        return report

    # ---------------------------------------------------------------- age limit
    def _sweep_by_age(self, library: Library, protect: set, report: SweepReport) -> None:
        cutoff = dt.date.today() - dt.timedelta(days=self.retention_days)
        for folder in library.camera_folders():
            for day in library.days(folder):
                if day >= cutoff:
                    continue
                day_dir = self.root / folder / day.isoformat()
                for clip in sorted(day_dir.glob("*.mp4")):
                    self._remove(clip, protect, report,
                                 f"older than {self.retention_days} days")

    # --------------------------------------------------------------- size limit
    def _sweep_by_size(self, library: Library, protect: set, report: SweepReport) -> None:
        limit = int(self.max_total_gb * 1024 ** 3)
        cap = int(limit * SIZE_HEADROOM)
        clips = library.all_clips()
        total = sum(c.size for c in clips)
        if total <= cap:
            return
        for clip in clips:                       # oldest first
            if total <= cap:
                break
            size = clip.size
            if self._remove(clip.path, protect, report, "over size limit"):
                total -= size

    # ------------------------------------------------------------------ helpers
    def _remove(self, path: Path, protect: set, report: SweepReport,
                reason: str) -> bool:
        if not _is_recording_clip(path, self.root):
            return False
        try:
            if str(path.resolve()) in protect:
                return False                     # currently being written
            size = path.stat().st_size
            path.unlink()
        except OSError as exc:
            report.errors.append(f"{path.name}: {exc}")
            return False
        report.removed_files += 1
        report.freed_bytes += size
        if reason not in report.reasons:
            report.reasons.append(reason)
        return True

    def _prune_empty_days(self, report: SweepReport) -> None:
        """Drop day folders left with no clips (never the camera folder itself)."""
        today = dt.date.today().isoformat()
        try:
            camera_dirs = [d for d in self.root.iterdir() if d.is_dir()]
        except OSError:
            return
        for camera_dir in camera_dirs:
            try:
                day_dirs = list(camera_dir.iterdir())
            except OSError:
                continue
            for day_dir in day_dirs:
                if not day_dir.is_dir() or day_dir.is_symlink():
                    continue
                if parse_day(day_dir.name) is None or day_dir.name == today:
                    continue
                try:
                    if any(day_dir.iterdir()):
                        continue
                    day_dir.rmdir()
                except OSError:
                    continue
                report.removed_days += 1

    # -------------------------------------------------------------------- disk
    def disk_free_bytes(self) -> int:
        if not self.root:
            return 0
        probe = self.root
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        try:
            return shutil.disk_usage(probe).free
        except OSError:
            return 0
