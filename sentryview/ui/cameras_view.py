"""Camera manager: the full list, with add / edit / remove / reorder."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .. import session as sess
from . import theme

STATE_TEXT = {
    sess.LIVE: "Live",
    sess.CONNECTING: "Connecting…",
    sess.RECONNECTING: "Reconnecting…",
    sess.ERROR: "Error",
    sess.STOPPED: "Stopped",
    sess.DISABLED: "Disabled",
}
STATE_COLOUR = {
    sess.LIVE: theme.OK,
    sess.CONNECTING: theme.WARN,
    sess.RECONNECTING: theme.WARN,
    sess.ERROR: theme.BAD,
    sess.STOPPED: theme.FAINT,
    sess.DISABLED: theme.FAINT,
}


class CamerasView(QWidget):
    addRequested = Signal()
    editRequested = Signal(str)
    removeRequested = Signal(str)
    reordered = Signal(list)            # camera ids, new order

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cameras: list = []
        self.states: dict = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Cameras")
        title.setProperty("role", "title")
        header.addWidget(title)
        self.count_label = QLabel("")
        self.count_label.setProperty("role", "subtitle")
        header.addWidget(self.count_label)
        header.addStretch(1)

        add = QPushButton("＋  Add camera")
        add.setProperty("variant", "primary")
        add.clicked.connect(self.addRequested.emit)
        header.addWidget(add)
        root.addLayout(header)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Name", "Address", "Transport", "Recording", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.doubleClicked.connect(lambda *_: self._edit_selected())
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        head = self.table.horizontalHeader()
        head.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft
                                 | Qt.AlignmentFlag.AlignVCenter)
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (2, 3, 4):
            head.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.up_button = QPushButton("Move up")
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button = QPushButton("Move down")
        self.down_button.clicked.connect(lambda: self._move(1))
        self.edit_button = QPushButton("Edit…")
        self.edit_button.clicked.connect(self._edit_selected)
        self.remove_button = QPushButton("Remove")
        self.remove_button.setProperty("variant", "danger")
        self.remove_button.clicked.connect(self._remove_selected)

        buttons.addWidget(self.up_button)
        buttons.addWidget(self.down_button)
        buttons.addStretch(1)
        buttons.addWidget(self.edit_button)
        buttons.addWidget(self.remove_button)
        root.addLayout(buttons)

        hint = QLabel("Grid order on the Live page follows this list. "
                      "Double-click a camera to edit it.")
        hint.setProperty("role", "hint")
        root.addWidget(hint)

        self._sync_buttons()

    # -------------------------------------------------------------------- data
    def set_cameras(self, cameras: list) -> None:
        self.cameras = list(cameras)
        selected = self._selected_id()
        self.table.setRowCount(len(self.cameras))
        for row, camera in enumerate(self.cameras):
            name = QTableWidgetItem(camera.name)
            name.setData(Qt.ItemDataRole.UserRole, camera.id)
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, QTableWidgetItem(camera.safe_url()))
            self.table.setItem(row, 2,
                               QTableWidgetItem((camera.transport or "tcp").upper()))
            self.table.setItem(row, 3,
                               QTableWidgetItem("Yes" if camera.record else "No"))
            self.table.setItem(row, 4, QTableWidgetItem("—"))
            self._paint_status(row, camera.id)

        count = len(self.cameras)
        self.count_label.setText(
            "" if not count else f"{count} camera{'s' if count != 1 else ''}")
        if selected:
            self.select(selected)
        self._sync_buttons()

    def set_state(self, camera_id: str, state: str, detail: str = "") -> None:
        self.states[camera_id] = (state, detail)
        for row, camera in enumerate(self.cameras):
            if camera.id == camera_id:
                self._paint_status(row, camera_id)
                return

    def _paint_status(self, row: int, camera_id: str) -> None:
        state, detail = self.states.get(camera_id, (None, ""))
        item = self.table.item(row, 4)
        if item is None:
            return
        if state is None:
            item.setText("—")
            return
        item.setText(STATE_TEXT.get(state, state))
        item.setForeground(Qt.GlobalColor.white)
        colour = STATE_COLOUR.get(state)
        if colour:
            from PySide6.QtGui import QColor
            item.setForeground(QColor(colour))
        item.setToolTip(detail or "")

    # --------------------------------------------------------------- selection
    def _selected_id(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def select(self, camera_id: str) -> None:
        for row, camera in enumerate(self.cameras):
            if camera.id == camera_id:
                self.table.selectRow(row)
                return

    def _sync_buttons(self) -> None:
        row = self.table.currentRow()
        has = row >= 0
        self.edit_button.setEnabled(has)
        self.remove_button.setEnabled(has)
        self.up_button.setEnabled(has and row > 0)
        self.down_button.setEnabled(has and row < len(self.cameras) - 1)

    def _edit_selected(self) -> None:
        camera_id = self._selected_id()
        if camera_id:
            self.editRequested.emit(camera_id)

    def _remove_selected(self) -> None:
        camera_id = self._selected_id()
        if camera_id:
            self.removeRequested.emit(camera_id)

    def _move(self, delta: int) -> None:
        row = self.table.currentRow()
        target = row + delta
        if row < 0 or not (0 <= target < len(self.cameras)):
            return
        order = [c.id for c in self.cameras]
        order[row], order[target] = order[target], order[row]
        self.reordered.emit(order)
