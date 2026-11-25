"""Find cameras on this network and hand one back to the Add Camera form."""
from __future__ import annotations

import threading

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog,
                               QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QProgressBar,
                               QPushButton, QVBoxLayout, QWidget)

from ..discovery import RTSP_PORTS, THOROUGH_PORTS, default_ranges, scan
from . import theme

CANDIDATE_ROLE = Qt.ItemDataRole.UserRole + 1


class ScanWorker(QThread):
    found = Signal(object)
    progressed = Signal(int, int, str)
    done = Signal(int)

    def __init__(self, ranges: list, thorough: bool, parent=None) -> None:
        super().__init__(parent)
        self.ranges = ranges
        self.thorough = thorough
        self.cancel = threading.Event()

    def stop(self) -> None:
        self.cancel.set()

    def run(self) -> None:
        results = scan(
            self.ranges,
            ports=THOROUGH_PORTS if self.thorough else RTSP_PORTS,
            on_progress=lambda d, t, n: self.progressed.emit(d, t, n),
            on_found=lambda c: self.found.emit(c),
            cancel=self.cancel,
        )
        self.done.emit(len(results))


class ScanDialog(QDialog):
    """Sweeps the local network and lets the user pick what it found."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Find cameras on this network")
        self.setModal(True)
        self.resize(680, 540)
        self.worker: ScanWorker | None = None
        self.selected = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)

        title = QLabel("Find cameras")
        title.setProperty("role", "title")
        root.addWidget(title)

        blurb = QLabel(
            "Looks for anything speaking RTSP on the network this computer is "
            "on, and works out the brand so the stream address can be filled "
            "in for you.")
        blurb.setProperty("role", "subtitle")
        blurb.setWordWrap(True)
        root.addWidget(blurb)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(QLabel("Range"))
        self.range_edit = QLineEdit(", ".join(default_ranges()))
        self.range_edit.setPlaceholderText("192.168.1.0/24")
        self.range_edit.setToolTip(
            "One or more ranges, comma separated.\n"
            "192.168.1.0/24   or   192.168.1.20-80")
        controls.addWidget(self.range_edit, 1)

        self.thorough = QCheckBox("Thorough")
        self.thorough.setToolTip("Check more ports. Slower, but catches "
                                 "cameras on unusual ones.")
        controls.addWidget(self.thorough)

        self.scan_button = QPushButton("Scan")
        self.scan_button.setProperty("variant", "primary")
        self.scan_button.clicked.connect(self._toggle_scan)
        controls.addWidget(self.scan_button)
        root.addLayout(controls)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setProperty("role", "hint")
        root.addWidget(self.status)

        self.results = QListWidget()
        self.results.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.results.itemDoubleClicked.connect(lambda _: self._accept())
        self.results.currentItemChanged.connect(
            lambda *_: self._sync_buttons())
        root.addWidget(self.results, 1)

        self.hint = QLabel("")
        self.hint.setProperty("role", "hint")
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.use_button = self.buttons.addButton(
            "Use this camera", QDialogButtonBox.ButtonRole.AcceptRole)
        self.use_button.setProperty("variant", "primary")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        self._sync_buttons()
        self._start_scan()

    # ------------------------------------------------------------------ scan
    def _toggle_scan(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.status.setText("Stopping…")
        else:
            self._start_scan()

    def _start_scan(self) -> None:
        ranges = [r.strip() for r in self.range_edit.text().split(",") if r.strip()]
        if not ranges:
            self.status.setText("Enter a range to scan, for example 192.168.1.0/24")
            return
        self.results.clear()
        self.hint.setText("")
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.scan_button.setText("Stop")
        self.status.setText("Starting…")

        self.worker = ScanWorker(ranges, self.thorough.isChecked(), self)
        self.worker.found.connect(self._on_found)
        self.worker.progressed.connect(self._on_progress)
        self.worker.done.connect(self._on_done)
        self.worker.start()
        self._sync_buttons()

    def _on_progress(self, done: int, total: int, note: str) -> None:
        if total > 1:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
        else:
            self.progress.setRange(0, 0)
        self.status.setText(note)

    def _on_found(self, candidate) -> None:
        item = QListWidgetItem(
            f"{candidate.host}"
            f"{'' if candidate.port == 554 else ':' + str(candidate.port)}"
            f"   —   {candidate.describe()}\n{candidate.suggested_url()}")
        item.setData(CANDIDATE_ROLE, candidate)
        item.setSizeHint(QSize(0, 56))
        self.results.addItem(item)
        if self.results.count() == 1:
            self.results.setCurrentRow(0)
        self._sync_buttons()

    def _on_done(self, count: int) -> None:
        self.progress.setVisible(False)
        self.scan_button.setText("Scan again")
        if count:
            self.status.setText(
                f"Found {count} camera{'s' if count != 1 else ''}.")
            self.hint.setText(
                "Pick one and it will fill in the address. You will still need "
                "the camera's RTSP username and password — these are usually "
                "set in the camera's own app, separately from the account you "
                "log in to it with.")
        else:
            self.status.setText("No cameras found.")
            self.hint.setText(
                "Nothing on this network answered on an RTSP port. Worth "
                "checking:\n"
                "  • this computer is on the same network as the camera, with "
                "no VPN connected\n"
                "  • RTSP (sometimes called ONVIF) is switched on in the "
                "camera's own settings — it ships disabled on many cameras, "
                "and some never support it at all\n"
                "  • the camera is on a different subnet — try Thorough, or "
                "type its range above")
        self._sync_buttons()

    # ----------------------------------------------------------------- picking
    def _sync_buttons(self) -> None:
        scanning = bool(self.worker and self.worker.isRunning())
        self.use_button.setEnabled(self.results.currentItem() is not None)
        self.range_edit.setEnabled(not scanning)
        self.thorough.setEnabled(not scanning)

    def _accept(self) -> None:
        item = self.results.currentItem()
        if item is None:
            return
        self.selected = item.data(CANDIDATE_ROLE)
        self._shutdown()
        self.accept()

    def _shutdown(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(8000)

    def reject(self) -> None:
        self._shutdown()
        super().reject()

    def closeEvent(self, event) -> None:
        self._shutdown()
        super().closeEvent(event)
