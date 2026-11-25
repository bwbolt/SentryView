"""Configuration model: cameras, storage policy, UI preferences.

Persisted as a single JSON document. Camera credentials are stored in
plain text, so the file is created with owner-only permissions.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from .paths import config_path, default_recordings_root

GRID_CHOICES = ["auto", "1x1", "2x2", "3x3", "4x4"]
SEGMENT_CHOICES = [5, 10, 15, 30, 60]


def _coerce(cls, data: dict) -> Any:
    """Build a dataclass from a dict, ignoring unknown keys."""
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in (data or {}).items() if k in known})


@dataclass
class Camera:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = "Camera"
    url: str = ""
    username: str = ""
    password: str = ""
    transport: str = "tcp"          # tcp | udp
    enabled: bool = True
    record: bool = True
    fix_timestamps: bool = False    # -use_wallclock_as_timestamps, for cameras with bad PTS

    def effective_url(self) -> str:
        """The RTSP URL with credentials injected, if they were given separately."""
        url = self.url.strip()
        if not url or not self.username:
            return url
        parts = urlsplit(url)
        if "@" in parts.netloc:      # credentials already inline; leave as-is
            return url
        cred = quote(self.username, safe="")
        if self.password:
            cred += ":" + quote(self.password, safe="")
        return urlunsplit(parts._replace(netloc=f"{cred}@{parts.netloc}"))

    def safe_url(self) -> str:
        """The URL with any password masked, for logs and the UI."""
        url = self.effective_url()
        parts = urlsplit(url)
        if "@" not in parts.netloc:
            return url
        cred, _, host = parts.netloc.rpartition("@")
        user = cred.split(":", 1)[0]
        masked = f"{user}:*****@{host}" if ":" in cred else f"{user}@{host}"
        return urlunsplit(parts._replace(netloc=masked))

    def folder_name(self) -> str:
        """Stable, filesystem-safe directory name for this camera."""
        keep = "-_ "
        cleaned = "".join(c for c in self.name if c.isalnum() or c in keep).strip()
        cleaned = cleaned.replace(" ", "_")[:48]
        return f"{cleaned or 'camera'}_{self.id[:6]}"


@dataclass
class Storage:
    enabled: bool = False
    root: str = ""
    segment_minutes: float = 10
    retention_days: int = 14        # 0 = keep forever
    max_total_gb: float = 0.0       # 0 = no size cap
    record_audio: bool = True

    def root_path(self) -> Path | None:
        return Path(self.root).expanduser() if self.root else None


@dataclass
class UiPrefs:
    grid: str = "auto"
    live_fps: int = 12
    tile_width: int = 640
    window_geometry: str = ""


@dataclass
class Config:
    version: int = 1
    onboarded: bool = False
    cameras: list = field(default_factory=list)
    storage: Storage = field(default_factory=Storage)
    ui: UiPrefs = field(default_factory=UiPrefs)

    # ---------------------------------------------------------------- load/save
    @classmethod
    def load(cls) -> "Config":
        path = config_path()
        if not path.exists():
            cfg = cls()
            cfg.storage.root = str(default_recordings_root())
            return cfg
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return cls()
        cfg = cls(
            version=raw.get("version", 1),
            onboarded=bool(raw.get("onboarded", False)),
            cameras=[_coerce(Camera, c) for c in raw.get("cameras", [])],
            storage=_coerce(Storage, raw.get("storage", {})),
            ui=_coerce(UiPrefs, raw.get("ui", {})),
        )
        if not cfg.storage.root:
            cfg.storage.root = str(default_recordings_root())
        return cfg

    def save(self) -> None:
        path = config_path()
        payload = {
            "version": self.version,
            "onboarded": self.onboarded,
            "cameras": [asdict(c) for c in self.cameras],
            "storage": asdict(self.storage),
            "ui": asdict(self.ui),
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), "utf-8")
        try:
            os.chmod(tmp, 0o600)        # credentials live in here
        except OSError:
            pass
        os.replace(tmp, path)

    # ------------------------------------------------------------------ cameras
    def camera(self, camera_id: str) -> Camera | None:
        return next((c for c in self.cameras if c.id == camera_id), None)

    def add_camera(self, cam: Camera) -> None:
        self.cameras.append(cam)

    def remove_camera(self, camera_id: str) -> None:
        self.cameras = [c for c in self.cameras if c.id != camera_id]

    def replace_camera(self, cam: Camera) -> None:
        for i, existing in enumerate(self.cameras):
            if existing.id == cam.id:
                self.cameras[i] = cam
                return
        self.cameras.append(cam)

    def unique_name(self, base: str) -> str:
        """A camera name not already in use."""
        existing = {c.name for c in self.cameras}
        if base not in existing:
            return base
        n = 2
        while f"{base} {n}" in existing:
            n += 1
        return f"{base} {n}"

    def recording_active(self) -> bool:
        return bool(self.storage.enabled and self.storage.root)
