"""Add / edit a camera, with a live connection test."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from ..config import Camera
from ..probe import ProbeWorker
from .scan_dialog import ScanDialog
from . import theme
from .widgets import VideoSurface

URL_HELP = (
    "Find the RTSP URL in your camera's app or manual. Common shapes:\n"
    "    Hikvision      rtsp://192.168.1.64:554/Streaming/Channels/101\n"
    "    Dahua / Amcrest rtsp://192.168.1.80:554/cam/realmonitor?channel=1&subtype=0\n"
    "    Reolink        rtsp://192.168.1.90:554/h264Preview_01_main\n"
    "    UniFi Protect  rtsp://192.168.1.1:7447/<stream key>\n"
    "    ONVIF generic  rtsp://192.168.1.50:554/onvif1"
)


class CameraDialog(QDialog):
    """Collects camera details; nothing is saved until the test-or-accept."""

    def __init__(self, camera: Camera | None = None, parent: QWidget | None = None,
                 existing_names: set | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Camera settings" if camera else "Add a camera")
        self.setModal(True)
        self.resize(760, 620)

        self.camera = Camera(**vars(camera)) if camera else Camera()
        self.existing_names = existing_names or set()
        self._worker: ProbeWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        heading = QLabel("Camera settings" if camera else "Add a camera")
        heading.setProperty("role", "title")
        root.addWidget(heading)

        subtitle = QLabel("Any camera on your local network that speaks RTSP.")
        subtitle.setProperty("role", "subtitle")
        root.addWidget(subtitle)

        body = QHBoxLayout()
        body.setSpacing(16)
        body.addWidget(self._build_form(), 3)
        body.addWidget(self._build_preview(), 2)
        root.addLayout(body, 1)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setProperty(
            "variant", "primary")
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText(
            "Save camera" if camera else "Add camera")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        self._load()

    # ------------------------------------------------------------------ building
    def _build_form(self) -> QWidget:
        panel = QFrame()
        panel.setProperty("role", "card")
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(9)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Front Door")
        form.addRow("Name", self.name_edit)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("rtsp://192.168.1.50:554/stream1")
        url_row = QHBoxLayout()
        url_row.setSpacing(6)
        url_row.addWidget(self.url_edit, 1)
        self.scan_button = QPushButton("Find cameras…")
        self.scan_button.setToolTip(
            "Scan this network for cameras and fill the address in")
        self.scan_button.clicked.connect(self._scan_network)
        url_row.addWidget(self.scan_button)
        form.addRow("RTSP URL", url_row)

        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("optional")
        form.addRow("Username", self.user_edit)

        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_edit.setPlaceholderText("optional")
        form.addRow("Password", self.pass_edit)

        self.transport_box = QComboBox()
        self.transport_box.addItem("TCP — reliable (recommended)", "tcp")
        self.transport_box.addItem("UDP — lower latency", "udp")
        form.addRow("Transport", self.transport_box)

        outer.addLayout(form)

        self.record_check = QCheckBox("Record this camera when saving is enabled")
        self.record_check.setChecked(True)
        outer.addWidget(self.record_check)

        self.fix_check = QCheckBox("Fix broken timestamps (try if clips play oddly)")
        outer.addWidget(self.fix_check)

        hint = QLabel(URL_HELP)
        hint.setProperty("role", "mono")
        hint.setWordWrap(False)
        hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(hint)
        outer.addStretch(1)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {theme.BAD}; font-size: 12px;")
        outer.addWidget(self.error_label)
        return panel

    def _build_preview(self) -> QWidget:
        panel = QFrame()
        panel.setProperty("role", "card")
        box = QVBoxLayout(panel)
        box.setContentsMargins(14, 14, 14, 14)
        box.setSpacing(8)

        caption = QLabel("Connection test")
        caption.setProperty("role", "section")
        box.addWidget(caption)

        self.preview = VideoSurface()
        self.preview.placeholder = "No test run yet"
        self.preview.setMinimumHeight(190)
        box.addWidget(self.preview, 1)

        self.test_button = QPushButton("Test connection")
        self.test_button.clicked.connect(self._test)
        box.addWidget(self.test_button)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setProperty("role", "subtitle")
        box.addWidget(self.status_label)

        self.hint_label = QLabel("")
        self.hint_label.setWordWrap(True)
        self.hint_label.setProperty("role", "hint")
        self.hint_label.setVisible(False)
        self.hint_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(self.hint_label)

        self.log_toggle = QPushButton("Show details")
        self.log_toggle.setProperty("variant", "ghost")
        self.log_toggle.setCheckable(True)
        self.log_toggle.toggled.connect(self._toggle_log)
        self.log_toggle.setVisible(False)
        box.addWidget(self.log_toggle)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(130)
        self.log_view.setVisible(False)
        self.log_view.setStyleSheet(
            f"font-family: {theme.mono_font()}; font-size: 10px;")
        box.addWidget(self.log_view)
        return panel

    # -------------------------------------------------------------------- state
    def _load(self) -> None:
        cam = self.camera
        self.name_edit.setText(cam.name if cam.name != "Camera" else "")
        self.url_edit.setText(cam.url)
        self.user_edit.setText(cam.username)
        self.pass_edit.setText(cam.password)
        index = self.transport_box.findData(cam.transport or "tcp")
        self.transport_box.setCurrentIndex(max(0, index))
        self.record_check.setChecked(cam.record)
        self.fix_check.setChecked(cam.fix_timestamps)

    def _collect(self) -> Camera:
        cam = self.camera
        cam.name = self.name_edit.text().strip() or "Camera"
        cam.url = self.url_edit.text().strip()
        cam.username = self.user_edit.text().strip()
        cam.password = self.pass_edit.text()
        cam.transport = self.transport_box.currentData()
        cam.record = self.record_check.isChecked()
        cam.fix_timestamps = self.fix_check.isChecked()
        return cam

    def result_camera(self) -> Camera:
        return self._collect()

    def _scan_network(self) -> None:
        dialog = ScanDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selected:
            return
        found = dialog.selected
        self.url_edit.setText(found.suggested_url())
        if not self.name_edit.text().strip():
            label = found.brand or "Camera"
            self.name_edit.setText(f"{label} {found.host.rsplit('.', 1)[-1]}")
        self._show_error("")
        note = "Address filled in. Add the camera's RTSP username and password, "
        note += "then press Test connection."
        self.status_label.setStyleSheet(f"color: {theme.MUTED}; font-size: 12px;")
        self.status_label.setText(note)
        (self.user_edit if found.needs_auth else self.url_edit).setFocus()

    # --------------------------------------------------------------------- test
    def _test(self) -> None:
        cam = self._collect()
        if not cam.url:
            self._show_error("Enter the camera's RTSP URL first.")
            return
        self._show_error("")
        self.test_button.setEnabled(False)
        self.test_button.setText("Testing…")
        self.status_label.setText("Connecting to the camera…")
        self.hint_label.setVisible(False)
        self.preview.placeholder = "Connecting…"
        self.preview.clear_frame()

        self._worker = ProbeWorker(cam.effective_url(), cam.transport, self)
        self._worker.done.connect(self._test_done)
        self._worker.start()

    def _test_done(self, result) -> None:
        self.test_button.setEnabled(True)
        self.test_button.setText("Test connection")
        colour = theme.OK if result.ok else theme.BAD
        self.status_label.setStyleSheet(f"color: {colour}; font-size: 12px;")
        self.status_label.setText(result.summary())

        if result.ok and result.image is not None:
            self.preview.set_frame(result.image)
        else:
            self.preview.placeholder = "No video"
            self.preview.clear_frame()

        hint = getattr(result, "hint", "")
        self.hint_label.setText(hint)
        self.hint_label.setVisible(bool(hint))

        self.log_view.setPlainText("\n".join(result.log))
        self.log_toggle.setVisible(bool(result.log))

    def _toggle_log(self, shown: bool) -> None:
        self.log_view.setVisible(shown)
        self.log_toggle.setText("Hide details" if shown else "Show details")

    def _show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.url_edit.setProperty("state", "error" if message else "")
        self.url_edit.style().unpolish(self.url_edit)
        self.url_edit.style().polish(self.url_edit)

    # ------------------------------------------------------------------- accept
    def _accept(self) -> None:
        cam = self._collect()
        url = cam.url
        if not url:
            self._show_error("A stream URL is required.")
            return
        if "://" not in url:
            self._show_error("That does not look like a URL. "
                             "It should start with rtsp://")
            return
        scheme = url.split("://", 1)[0].lower()
        if scheme not in ("rtsp", "rtsps", "http", "https", "rtmp"):
            self._show_error(f"Unsupported URL type '{scheme}://'. Use rtsp://")
            return
        if cam.name in self.existing_names:
            self._show_error(f"You already have a camera named “{cam.name}”.")
            return
        self.accept()

    def closeEvent(self, event) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(3000)
        super().closeEvent(event)
