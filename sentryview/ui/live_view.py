"""The live wall: a responsive grid of camera tiles."""
from __future__ import annotations

import math

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QAction, QImage
from PySide6.QtWidgets import (QButtonGroup, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QMenu, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from .. import session as sess
from ..config import Camera
from . import theme
from .widgets import CameraTile

LAYOUTS = {"auto": 0, "1x1": 1, "2x2": 2, "3x3": 3, "4x4": 4}


class LiveView(QWidget):
    addCameraRequested = Signal()
    editCameraRequested = Signal(str)
    removeCameraRequested = Signal(str)
    snapshotRequested = Signal(str)
    viewRecordingsRequested = Signal(str)
    toggleRecordRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tiles: dict = {}
        self.cameras: list = []
        self.layout_mode = "auto"
        self.maximized: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 16)
        root.setSpacing(12)
        root.addWidget(self._build_toolbar())

        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(8)
        root.addWidget(self.grid_host, 1)

        self.empty = self._build_empty_state()
        root.addWidget(self.empty, 1)

    # ------------------------------------------------------------------ chrome
    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        title = QLabel("Live")
        title.setProperty("role", "title")
        row.addWidget(title)

        self.count_label = QLabel("")
        self.count_label.setProperty("role", "subtitle")
        row.addWidget(self.count_label)
        row.addStretch(1)

        self.layout_group = QButtonGroup(self)
        self.layout_group.setExclusive(True)
        for name in LAYOUTS:
            button = QPushButton(name.replace("auto", "Auto"))
            button.setCheckable(True)
            button.setChecked(name == "auto")
            button.setFixedWidth(58)
            button.clicked.connect(lambda _=False, n=name: self.set_layout(n))
            self.layout_group.addButton(button)
            row.addWidget(button)

        add = QPushButton("＋  Add camera")
        add.setProperty("variant", "primary")
        add.clicked.connect(self.addCameraRequested.emit)
        row.addSpacing(8)
        row.addWidget(add)
        return bar

    def _build_empty_state(self) -> QWidget:
        frame = QFrame()
        frame.setProperty("role", "card")
        box = QVBoxLayout(frame)
        box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.setSpacing(10)

        icon = QLabel("🎥")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("font-size: 44px;")
        headline = QLabel("No cameras yet")
        headline.setProperty("role", "title")
        headline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        blurb = QLabel("Add any camera on your network by its RTSP URL.\n"
                       "You can add as many as you like.")
        blurb.setProperty("role", "subtitle")
        blurb.setAlignment(Qt.AlignmentFlag.AlignCenter)

        button = QPushButton("Add your first camera")
        button.setProperty("variant", "primary")
        button.clicked.connect(self.addCameraRequested.emit)

        wrap = QHBoxLayout()
        wrap.addStretch(1)
        wrap.addWidget(button)
        wrap.addStretch(1)

        box.addWidget(icon)
        box.addWidget(headline)
        box.addWidget(blurb)
        box.addLayout(wrap)
        return frame

    # ------------------------------------------------------------------- tiles
    def set_cameras(self, cameras: list) -> None:
        """Rebuild tiles to match the configured camera list."""
        self.cameras = list(cameras)
        wanted = {c.id for c in cameras}

        for camera_id in list(self.tiles):
            if camera_id not in wanted:
                tile = self.tiles.pop(camera_id)
                tile.setParent(None)
                tile.deleteLater()

        for camera in cameras:
            tile = self.tiles.get(camera.id)
            if tile is None:
                tile = CameraTile(camera.id, camera.name)
                tile.requestMaximize.connect(self.toggle_maximize)
                tile.requestMenu.connect(self._show_tile_menu)
                self.tiles[camera.id] = tile
            else:
                tile.name = camera.name
            if not camera.enabled:
                tile.set_state(sess.DISABLED)
            tile.update()

        if self.maximized and self.maximized not in wanted:
            self.maximized = None

        shown = len(cameras)
        self.count_label.setText(
            "" if not shown else f"{shown} camera{'s' if shown != 1 else ''}")
        self.empty.setVisible(shown == 0)
        self.grid_host.setVisible(shown > 0)
        self.relayout()

    def relayout(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        visible = [c for c in self.cameras]
        if not visible:
            return

        if self.maximized:
            tile = self.tiles.get(self.maximized)
            if tile:
                tile.setParent(self.grid_host)
                tile.show()
                self.grid.addWidget(tile, 0, 0)
                for camera in visible:
                    if camera.id != self.maximized:
                        other = self.tiles.get(camera.id)
                        if other:
                            other.hide()
                return

        columns = LAYOUTS.get(self.layout_mode, 0)
        if columns == 0:
            columns = max(1, math.ceil(math.sqrt(len(visible))))

        for index, camera in enumerate(visible):
            tile = self.tiles.get(camera.id)
            if tile is None:
                continue
            tile.setParent(self.grid_host)
            tile.show()
            self.grid.addWidget(tile, index // columns, index % columns)

        for column in range(columns):
            self.grid.setColumnStretch(column, 1)
        rows = max(1, math.ceil(len(visible) / columns))
        for row in range(rows):
            self.grid.setRowStretch(row, 1)

    def set_layout(self, name: str) -> None:
        self.layout_mode = name
        self.maximized = None
        self.relayout()

    def toggle_maximize(self, camera_id: str) -> None:
        self.maximized = None if self.maximized == camera_id else camera_id
        self.relayout()

    # ----------------------------------------------------------------- updates
    def on_frame(self, camera_id: str, image: QImage) -> None:
        tile = self.tiles.get(camera_id)
        if tile is not None and tile.isVisible():
            tile.set_frame(image)

    def on_state(self, camera_id: str, state: str, detail: str) -> None:
        tile = self.tiles.get(camera_id)
        if tile is not None:
            tile.set_state(state, detail)

    def set_recording(self, camera_id: str, recording: bool) -> None:
        tile = self.tiles.get(camera_id)
        if tile is not None and tile.recording != recording:
            tile.recording = recording
            tile.update()

    def tile_frame(self, camera_id: str) -> QImage | None:
        tile = self.tiles.get(camera_id)
        return tile.current_frame() if tile else None

    # ------------------------------------------------------------------- menu
    def _show_tile_menu(self, camera_id: str, position: QPoint) -> None:
        camera = next((c for c in self.cameras if c.id == camera_id), None)
        if camera is None:
            return
        menu = QMenu(self)

        maximize = QAction("Restore grid" if self.maximized == camera_id
                           else "Maximize", menu)
        maximize.triggered.connect(lambda: self.toggle_maximize(camera_id))
        menu.addAction(maximize)

        snapshot = QAction("Save snapshot…", menu)
        snapshot.triggered.connect(lambda: self.snapshotRequested.emit(camera_id))
        menu.addAction(snapshot)

        recordings = QAction("View recordings", menu)
        recordings.triggered.connect(
            lambda: self.viewRecordingsRequested.emit(camera_id))
        menu.addAction(recordings)
        menu.addSeparator()

        record = QAction("Stop recording this camera" if camera.record
                         else "Record this camera", menu)
        record.triggered.connect(lambda: self.toggleRecordRequested.emit(camera_id))
        menu.addAction(record)

        edit = QAction("Camera settings…", menu)
        edit.triggered.connect(lambda: self.editCameraRequested.emit(camera_id))
        menu.addAction(edit)
        menu.addSeparator()

        remove = QAction("Remove camera", menu)
        remove.triggered.connect(lambda: self.removeCameraRequested.emit(camera_id))
        menu.addAction(remove)

        menu.exec(position)
