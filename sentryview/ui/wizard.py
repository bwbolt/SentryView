"""First-run setup. Every question here is optional and changeable later."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QDoubleSpinBox, QFileDialog, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QRadioButton,
                               QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from ..config import SEGMENT_CHOICES, Camera, Storage
from ..paths import default_recordings_root
from . import theme
from .camera_dialog import CameraDialog


class SetupWizard(QDialog):
    """Returns a Storage policy and any cameras added along the way."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Welcome to SentryView")
        self.setModal(True)
        self.resize(720, 580)

        self.storage = Storage(root=str(default_recordings_root()))
        self.cameras: list = []

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 20)
        root.setSpacing(16)

        self.step_label = QLabel("")
        self.step_label.setProperty("role", "section")
        root.addWidget(self.step_label)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._page_welcome())
        self.pages.addWidget(self._page_storage())
        self.pages.addWidget(self._page_cameras())
        root.addWidget(self.pages, 1)

        nav = QHBoxLayout()
        self.back_button = QPushButton("Back")
        self.back_button.setProperty("variant", "ghost")
        self.back_button.clicked.connect(self._back)
        nav.addWidget(self.back_button)
        nav.addStretch(1)

        self.skip_button = QPushButton("Skip setup")
        self.skip_button.setProperty("variant", "ghost")
        self.skip_button.clicked.connect(self._finish)
        nav.addWidget(self.skip_button)

        self.next_button = QPushButton("Continue")
        self.next_button.setProperty("variant", "primary")
        self.next_button.clicked.connect(self._next)
        nav.addWidget(self.next_button)
        root.addLayout(nav)

        self._sync_nav()

    # -------------------------------------------------------------------- pages
    def _page_welcome(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setSpacing(14)

        title = QLabel("Watch and record your cameras, locally")
        title.setProperty("role", "title")
        box.addWidget(title)

        blurb = QLabel(
            "SentryView connects straight to the cameras on your own network. "
            "Nothing leaves this computer — there is no account and no cloud.")
        blurb.setProperty("role", "subtitle")
        blurb.setWordWrap(True)
        box.addWidget(blurb)

        for heading, detail in [
            ("Add any number of RTSP cameras",
             "Paste the stream URL from your camera's app and you are done."),
            ("Record continuously, split by day",
             "Optional. Clips land in dated folders so finding a moment is easy."),
            ("Keep storage under control",
             "Optional age and size limits delete the oldest footage for you."),
        ]:
            card = QFrame()
            card.setProperty("role", "card")
            inner = QVBoxLayout(card)
            inner.setContentsMargins(14, 12, 14, 12)
            inner.setSpacing(3)
            label = QLabel(heading)
            label.setStyleSheet("font-weight: 600; font-size: 13px;")
            note = QLabel(detail)
            note.setProperty("role", "subtitle")
            note.setWordWrap(True)
            inner.addWidget(label)
            inner.addWidget(note)
            box.addWidget(card)

        box.addStretch(1)
        return page

    def _page_storage(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setSpacing(12)

        title = QLabel("Do you want to save video?")
        title.setProperty("role", "title")
        box.addWidget(title)

        blurb = QLabel("You can skip this and watch live only. "
                       "Recording can be switched on at any time.")
        blurb.setProperty("role", "subtitle")
        blurb.setWordWrap(True)
        box.addWidget(blurb)

        self.storage_group = QButtonGroup(self)
        self.save_radio = QRadioButton("Save video to this computer")
        self.live_radio = QRadioButton("Live view only — don't save anything")
        self.live_radio.setChecked(True)
        self.storage_group.addButton(self.save_radio)
        self.storage_group.addButton(self.live_radio)
        self.save_radio.toggled.connect(self._on_save_toggled)
        box.addWidget(self.save_radio)

        self.detail_card = QFrame()
        self.detail_card.setProperty("role", "card")
        detail = QVBoxLayout(self.detail_card)
        detail.setContentsMargins(16, 14, 16, 14)
        detail.setSpacing(10)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Folder"))
        self.folder_edit = QLineEdit(self.storage.root)
        folder_row.addWidget(self.folder_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        folder_row.addWidget(browse)
        detail.addLayout(folder_row)

        clip_row = QHBoxLayout()
        clip_row.addWidget(QLabel("Clip length"))
        self.segment_box = QComboBox()
        for minutes in SEGMENT_CHOICES:
            self.segment_box.addItem(f"{minutes} minutes", minutes)
        self.segment_box.setCurrentIndex(SEGMENT_CHOICES.index(10))
        self.segment_box.setFixedWidth(140)
        clip_row.addWidget(self.segment_box)
        clip_row.addStretch(1)
        detail.addLayout(clip_row)

        limits = QLabel("How long should clips be kept?")
        limits.setProperty("role", "section")
        detail.addWidget(limits)

        age_row = QHBoxLayout()
        self.age_check = QCheckBox("Delete clips older than")
        self.age_check.setChecked(True)
        self.age_check.toggled.connect(
            lambda on: self.age_spin.setEnabled(on))
        age_row.addWidget(self.age_check)
        self.age_spin = QSpinBox()
        self.age_spin.setRange(1, 3650)
        self.age_spin.setValue(14)
        self.age_spin.setSuffix(" days")
        self.age_spin.setFixedWidth(110)
        age_row.addWidget(self.age_spin)
        age_row.addStretch(1)
        detail.addLayout(age_row)

        size_row = QHBoxLayout()
        self.size_check = QCheckBox("Keep total recordings under")
        self.size_check.toggled.connect(
            lambda on: self.size_spin.setEnabled(on))
        size_row.addWidget(self.size_check)
        self.size_spin = QDoubleSpinBox()
        self.size_spin.setRange(1, 100000)
        self.size_spin.setDecimals(0)
        self.size_spin.setValue(200)
        self.size_spin.setSuffix(" GB")
        self.size_spin.setFixedWidth(120)
        self.size_spin.setEnabled(False)
        size_row.addWidget(self.size_spin)
        size_row.addStretch(1)
        detail.addLayout(size_row)

        note = QLabel("Both limits are optional. With neither set, recordings grow "
                      "until you remove them yourself.")
        note.setProperty("role", "hint")
        note.setWordWrap(True)
        detail.addWidget(note)

        box.addWidget(self.detail_card)
        box.addWidget(self.live_radio)
        box.addStretch(1)
        self._on_save_toggled(False)
        return page

    def _page_cameras(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setSpacing(12)

        title = QLabel("Add your first camera")
        title.setProperty("role", "title")
        box.addWidget(title)

        blurb = QLabel(
            "You will need the camera's RTSP URL, which its own app or manual "
            "lists. You can add more cameras later, as many as you like.")
        blurb.setProperty("role", "subtitle")
        blurb.setWordWrap(True)
        box.addWidget(blurb)

        add = QPushButton("＋  Add a camera")
        add.setProperty("variant", "primary")
        add.clicked.connect(self._add_camera)
        box.addWidget(add)

        self.camera_summary = QLabel("No cameras added yet.")
        self.camera_summary.setProperty("role", "subtitle")
        self.camera_summary.setWordWrap(True)
        box.addWidget(self.camera_summary)

        helper = QFrame()
        helper.setProperty("role", "card")
        inner = QVBoxLayout(helper)
        inner.setContentsMargins(14, 12, 14, 12)
        heading = QLabel("No camera handy?")
        heading.setStyleSheet("font-weight: 600;")
        inner.addWidget(heading)
        note = QLabel(
            "SentryView ships a test camera you can point it at. Run this from "
            "the app folder, then add rtsp://127.0.0.1:8554/cam1\n\n"
            "    python3 tools/test_camera.py")
        note.setProperty("role", "mono")
        note.setWordWrap(True)
        inner.addWidget(note)
        box.addWidget(helper)
        box.addStretch(1)
        return page

    # --------------------------------------------------------------------- nav
    def _on_save_toggled(self, enabled: bool) -> None:
        self.detail_card.setEnabled(enabled)
        self.age_spin.setEnabled(enabled and self.age_check.isChecked())
        self.size_spin.setEnabled(enabled and self.size_check.isChecked())

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose a recordings folder",
            self.folder_edit.text() or str(Path.home()))
        if chosen:
            self.folder_edit.setText(chosen)

    def _add_camera(self) -> None:
        dialog = CameraDialog(parent=self,
                              existing_names={c.name for c in self.cameras})
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.cameras.append(dialog.result_camera())
            names = ", ".join(c.name for c in self.cameras)
            self.camera_summary.setText(
                f"{len(self.cameras)} camera(s) ready: {names}")

    def _sync_nav(self) -> None:
        index = self.pages.currentIndex()
        self.step_label.setText(f"Step {index + 1} of {self.pages.count()}")
        self.back_button.setVisible(index > 0)
        last = index == self.pages.count() - 1
        self.next_button.setText("Finish" if last else "Continue")
        self.skip_button.setVisible(not last)

    def _next(self) -> None:
        if self.pages.currentIndex() == self.pages.count() - 1:
            self._finish()
            return
        self.pages.setCurrentIndex(self.pages.currentIndex() + 1)
        self._sync_nav()

    def _back(self) -> None:
        self.pages.setCurrentIndex(max(0, self.pages.currentIndex() - 1))
        self._sync_nav()

    def _finish(self) -> None:
        if self.save_radio.isChecked():
            self.storage = Storage(
                enabled=True,
                root=self.folder_edit.text().strip() or str(default_recordings_root()),
                segment_minutes=self.segment_box.currentData() or 10,
                retention_days=(self.age_spin.value()
                                if self.age_check.isChecked() else 0),
                max_total_gb=(self.size_spin.value()
                              if self.size_check.isChecked() else 0.0),
                record_audio=True,
            )
        else:
            self.storage = Storage(enabled=False,
                                   root=str(default_recordings_root()))
        self.accept()
