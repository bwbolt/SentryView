"""Storage page: where clips go, how long they stay, how much space they may use."""
from __future__ import annotations

import datetime as dt

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QSpinBox, QVBoxLayout,
                               QWidget)

from ..config import SEGMENT_CHOICES, Storage
from ..library import Library
from ..retention import RetentionManager
from ..util import human_duration, human_size, open_path
from . import theme
from .widgets import StatCard


class StorageView(QWidget):
    """Edits the storage policy; emits the new policy on Apply."""

    applied = Signal(object)            # Storage
    cleanupRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.storage = Storage()
        self._loading = False

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(16, 12, 16, 16)
        root.setSpacing(14)

        title = QLabel("Storage")
        title.setProperty("role", "title")
        root.addWidget(title)

        subtitle = QLabel("Saving video is optional. Turn it on and every recording "
                          "camera writes continuous clips, split by day.")
        subtitle.setProperty("role", "subtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        root.addWidget(self._build_destination())
        root.addWidget(self._build_limits())
        root.addWidget(self._build_stats())
        root.addStretch(1)

        buttons = QHBoxLayout()
        self.dirty_label = QLabel("")
        self.dirty_label.setProperty("role", "hint")
        buttons.addWidget(self.dirty_label)
        buttons.addStretch(1)

        self.revert_button = QPushButton("Revert")
        self.revert_button.setProperty("variant", "ghost")
        self.revert_button.clicked.connect(lambda: self.load(self.storage))
        buttons.addWidget(self.revert_button)

        self.apply_button = QPushButton("Apply changes")
        self.apply_button.setProperty("variant", "primary")
        self.apply_button.clicked.connect(self._apply)
        buttons.addWidget(self.apply_button)
        root.addLayout(buttons)

        scroll.setWidget(page)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self._set_dirty(False)

    # ------------------------------------------------------------------ building
    def _card(self, title: str, blurb: str = "") -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setProperty("role", "card")
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 14, 16, 16)
        box.setSpacing(10)
        label = QLabel(title)
        label.setProperty("role", "section")
        box.addWidget(label)
        if blurb:
            hint = QLabel(blurb)
            hint.setProperty("role", "hint")
            hint.setWordWrap(True)
            box.addWidget(hint)
        return card, box

    def _build_destination(self) -> QWidget:
        card, box = self._card("Destination")

        self.enable_check = QCheckBox("Save video to this computer")
        self.enable_check.toggled.connect(self._on_enable_toggled)
        box.addWidget(self.enable_check)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("Choose a folder…")
        self.folder_edit.textChanged.connect(lambda: self._set_dirty(True))
        row.addWidget(self.folder_edit, 1)

        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)

        self.open_button = QPushButton("Open")
        self.open_button.setProperty("variant", "ghost")
        self.open_button.clicked.connect(self._open_folder)
        row.addWidget(self.open_button)
        box.addLayout(row)

        self.layout_hint = QLabel("")
        self.layout_hint.setProperty("role", "mono")
        box.addWidget(self.layout_hint)

        clip_row = QHBoxLayout()
        clip_row.setSpacing(8)
        clip_row.addWidget(QLabel("Split recordings into clips of"))
        self.segment_box = QComboBox()
        for minutes in SEGMENT_CHOICES:
            self.segment_box.addItem(f"{minutes} minutes", minutes)
        self.segment_box.setFixedWidth(140)
        self.segment_box.currentIndexChanged.connect(lambda: self._set_dirty(True))
        clip_row.addWidget(self.segment_box)
        clip_row.addStretch(1)
        box.addLayout(clip_row)

        self.audio_check = QCheckBox("Record audio when the camera provides it")
        self.audio_check.toggled.connect(lambda: self._set_dirty(True))
        box.addWidget(self.audio_check)
        return card

    def _build_limits(self) -> QWidget:
        card, box = self._card(
            "Limits",
            "Both limits are optional. Leave them off to keep everything until "
            "you delete it yourself.")

        age_row = QHBoxLayout()
        age_row.setSpacing(8)
        self.age_check = QCheckBox("Delete clips older than")
        self.age_check.toggled.connect(self._on_limit_toggled)
        age_row.addWidget(self.age_check)
        self.age_spin = QSpinBox()
        self.age_spin.setRange(1, 3650)
        self.age_spin.setValue(14)
        self.age_spin.setSuffix(" days")
        self.age_spin.setFixedWidth(110)
        self.age_spin.valueChanged.connect(lambda: self._set_dirty(True))
        age_row.addWidget(self.age_spin)
        age_row.addStretch(1)
        box.addLayout(age_row)

        size_row = QHBoxLayout()
        size_row.setSpacing(8)
        self.size_check = QCheckBox("Keep total recordings under")
        self.size_check.toggled.connect(self._on_limit_toggled)
        size_row.addWidget(self.size_check)
        self.size_spin = QDoubleSpinBox()
        self.size_spin.setRange(1.0, 100000.0)
        self.size_spin.setDecimals(0)
        self.size_spin.setValue(200)
        self.size_spin.setSuffix(" GB")
        self.size_spin.setFixedWidth(120)
        self.size_spin.valueChanged.connect(lambda: self._set_dirty(True))
        size_row.addWidget(self.size_spin)
        size_row.addStretch(1)
        box.addLayout(size_row)

        note = QLabel("When the size limit is reached the oldest clips are removed "
                      "first. The clip being written right now is never touched.")
        note.setProperty("role", "hint")
        note.setWordWrap(True)
        box.addWidget(note)
        return card

    def _build_stats(self) -> QWidget:
        card, box = self._card("Usage")

        grid = QGridLayout()
        grid.setSpacing(10)
        self.used_card = StatCard("Recordings")
        self.free_card = StatCard("Free on disk")
        self.span_card = StatCard("Oldest clip")
        self.rate_card = StatCard("Estimated capacity")
        for index, widget in enumerate((self.used_card, self.free_card,
                                        self.span_card, self.rate_card)):
            grid.addWidget(widget, 0, index)
        box.addLayout(grid)

        row = QHBoxLayout()
        self.sweep_label = QLabel("")
        self.sweep_label.setProperty("role", "hint")
        row.addWidget(self.sweep_label)
        row.addStretch(1)
        cleanup = QPushButton("Apply limits now")
        cleanup.clicked.connect(self.cleanupRequested.emit)
        row.addWidget(cleanup)
        box.addLayout(row)
        return card

    # -------------------------------------------------------------------- state
    def load(self, storage: Storage) -> None:
        self.storage = storage
        self._loading = True
        self.enable_check.setChecked(storage.enabled)
        self.folder_edit.setText(storage.root)
        index = self.segment_box.findData(int(storage.segment_minutes))
        if index < 0:
            self.segment_box.addItem(f"{storage.segment_minutes:g} minutes",
                                     int(storage.segment_minutes) or 10)
            index = self.segment_box.count() - 1
        self.segment_box.setCurrentIndex(index)
        self.audio_check.setChecked(storage.record_audio)

        self.age_check.setChecked(bool(storage.retention_days))
        if storage.retention_days:
            self.age_spin.setValue(int(storage.retention_days))
        self.size_check.setChecked(bool(storage.max_total_gb))
        if storage.max_total_gb:
            self.size_spin.setValue(float(storage.max_total_gb))

        self._loading = False
        self._on_enable_toggled(storage.enabled)
        self._set_dirty(False)
        self.refresh_stats()

    def collect(self) -> Storage:
        return Storage(
            enabled=self.enable_check.isChecked(),
            root=self.folder_edit.text().strip(),
            segment_minutes=self.segment_box.currentData() or 10,
            retention_days=self.age_spin.value() if self.age_check.isChecked() else 0,
            max_total_gb=self.size_spin.value() if self.size_check.isChecked() else 0.0,
            record_audio=self.audio_check.isChecked(),
        )

    def _on_enable_toggled(self, enabled: bool) -> None:
        for widget in (self.folder_edit, self.segment_box, self.audio_check,
                       self.age_check, self.size_check):
            widget.setEnabled(enabled)
        self._on_limit_toggled()
        self._update_layout_hint()
        if not self._loading:
            self._set_dirty(True)

    def _on_limit_toggled(self, *_) -> None:
        enabled = self.enable_check.isChecked()
        self.age_spin.setEnabled(enabled and self.age_check.isChecked())
        self.size_spin.setEnabled(enabled and self.size_check.isChecked())
        if not self._loading:
            self._set_dirty(True)

    def _set_dirty(self, dirty: bool) -> None:
        if self._loading:
            return
        self.apply_button.setEnabled(dirty)
        self.revert_button.setEnabled(dirty)
        self.dirty_label.setText("Unsaved changes" if dirty else "")

    def _apply(self) -> None:
        storage = self.collect()
        if storage.enabled and not storage.root:
            self._browse()
            storage = self.collect()
            if not storage.root:
                return
        self.storage = storage
        self._set_dirty(False)
        self.applied.emit(storage)

    # ------------------------------------------------------------------- actions
    def _browse(self) -> None:
        start = self.folder_edit.text().strip() or str(
            __import__("pathlib").Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Choose a recordings folder",
                                                  start)
        if chosen:
            self.folder_edit.setText(chosen)
            self._update_layout_hint()

    def _open_folder(self) -> None:
        path = self.folder_edit.text().strip()
        if path:
            from pathlib import Path
            target = Path(path).expanduser()
            if target.exists():
                open_path(target)

    def _update_layout_hint(self) -> None:
        root = self.folder_edit.text().strip()
        if not root or not self.enable_check.isChecked():
            self.layout_hint.setText("")
            return
        today = dt.date.today().isoformat()
        self.layout_hint.setText(
            f"Clips are written as:  {root}/<camera>/{today}/14-30-00.mp4")

    def refresh_stats(self, sweep_note: str = "") -> None:
        storage = self.collect()
        library = Library(storage.root if storage.root else None)
        manager = RetentionManager(storage.root, storage.retention_days,
                                   storage.max_total_gb)

        used = library.total_bytes() if library.available() else 0
        clips = library.all_clips() if library.available() else []
        covered = sum(c.duration for c in clips)

        self.used_card.set_value(human_size(used),
                                 f"{len(clips)} clips" if clips else "nothing yet")
        free = manager.disk_free_bytes()
        self.free_card.set_value(human_size(free) if free else "—",
                                 "on this volume" if free else "")

        oldest = library.oldest_day() if library.available() else None
        if oldest:
            age = (dt.date.today() - oldest).days
            self.span_card.set_value(oldest.strftime("%b %d"),
                                     f"{age} day{'s' if age != 1 else ''} of history")
        else:
            self.span_card.set_value("—", "")

        # Project remaining capacity from the rate already observed on disk.
        if covered > 60 and used > 0:
            per_day = used / covered * 86400
            budget = (storage.max_total_gb * 1024 ** 3) if storage.max_total_gb else free
            if per_day > 0 and budget > 0:
                days = budget / per_day
                self.rate_card.set_value(
                    f"~{days:.0f} days",
                    f"{human_size(per_day)}/day at the current rate")
            else:
                self.rate_card.set_value("—", "")
        else:
            self.rate_card.set_value("—", "needs more footage to estimate")

        if sweep_note:
            self.sweep_label.setText(sweep_note)
        self._update_layout_hint()
