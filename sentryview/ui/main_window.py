"""Main window, which doubles as the application controller.

It owns the configuration, the per-camera sessions and the retention timer,
and keeps the four views in sync with them.
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QDialog, QFileDialog, QLabel, QMainWindow,
                               QMessageBox, QTabWidget, QWidget)

from .. import APP_NAME, __version__, session as sess
from ..config import Camera, Config, Storage
from ..library import Library
from ..paths import app_data_dir, find_ffmpeg
from ..retention import RetentionManager
from ..util import human_size, open_path
from . import theme
from .camera_dialog import CameraDialog
from .cameras_view import CamerasView
from .live_view import LiveView
from .playback_view import PlaybackView
from .storage_view import StorageView
from .wizard import SetupWizard

# An age limit only changes at midnight, so a lazy sweep is fine. A size cap
# is different: bytes land continuously, and a five-minute gap is long enough
# for several cameras to overshoot the limit by gigabytes.
RETENTION_INTERVAL_MS = 5 * 60 * 1000
RETENTION_INTERVAL_CAPPED_MS = 60 * 1000
MIN_SWEEP_GAP_S = 5
STATUS_INTERVAL_MS = 4000

TAB_LIVE, TAB_RECORDINGS, TAB_CAMERAS, TAB_STORAGE = range(4)


class MainWindow(QMainWindow):
    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.sessions: dict = {}
        self._shutting_down = False
        self._last_sweep = ""
        self._last_sweep_at = 0.0

        self.setWindowTitle(APP_NAME)
        self.resize(1360, 880)
        self.setMinimumSize(980, 640)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        self.live = LiveView()
        self.playback = PlaybackView()
        self.cameras_view = CamerasView()
        self.storage_view = StorageView()

        self.tabs.addTab(self.live, "Live")
        self.tabs.addTab(self.playback, "Recordings")
        self.tabs.addTab(self.cameras_view, "Cameras")
        self.tabs.addTab(self.storage_view, "Storage")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)

        self._build_status_bar()
        self._build_menus()
        self._wire()

        self.retention_timer = QTimer(self)
        self.retention_timer.timeout.connect(lambda: self.run_retention(False))
        self._sync_retention_interval()

        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._refresh_status)
        self.status_timer.start(STATUS_INTERVAL_MS)

        self._restore_geometry()
        self.refresh_all()

    # ------------------------------------------------------------------ chrome
    def _build_status_bar(self) -> None:
        bar = self.statusBar()
        self.record_status = QLabel("")
        self.storage_status = QLabel("")
        self.policy_status = QLabel("")
        bar.addWidget(self.record_status)
        bar.addWidget(QLabel("  "))
        bar.addWidget(self.storage_status, 1)
        bar.addPermanentWidget(self.policy_status)

    def _build_menus(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        add = QAction("Add Camera…", self)
        add.setShortcut(QKeySequence.StandardKey.New)
        add.triggered.connect(self.add_camera)
        file_menu.addAction(add)

        open_folder = QAction("Open Recordings Folder", self)
        open_folder.triggered.connect(self._open_recordings_folder)
        file_menu.addAction(open_folder)

        settings = QAction("Storage Settings…", self)
        settings.setShortcut(QKeySequence("Ctrl+,"))
        settings.triggered.connect(lambda: self.tabs.setCurrentIndex(TAB_STORAGE))
        file_menu.addAction(settings)
        file_menu.addSeparator()

        rerun = QAction("Run Setup Again…", self)
        rerun.triggered.connect(self.run_wizard)
        file_menu.addAction(rerun)
        file_menu.addSeparator()

        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = menubar.addMenu("&View")
        for index, (name, shortcut) in enumerate([
                ("Live", "Ctrl+1"), ("Recordings", "Ctrl+2"),
                ("Cameras", "Ctrl+3"), ("Storage", "Ctrl+4")]):
            action = QAction(name, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(
                lambda _=False, i=index: self.tabs.setCurrentIndex(i))
            view_menu.addAction(action)
        view_menu.addSeparator()

        for name in ("auto", "1x1", "2x2", "3x3", "4x4"):
            action = QAction(f"Grid: {name.replace('auto', 'Auto')}", self)
            action.triggered.connect(
                lambda _=False, n=name: (self.tabs.setCurrentIndex(TAB_LIVE),
                                         self.live.set_layout(n)))
            view_menu.addAction(action)
        view_menu.addSeparator()

        fullscreen = QAction("Toggle Full Screen", self)
        fullscreen.setShortcut(QKeySequence.StandardKey.FullScreen)
        fullscreen.triggered.connect(self._toggle_fullscreen)
        view_menu.addAction(fullscreen)

        help_menu = menubar.addMenu("&Help")
        about = QAction(f"About {APP_NAME}", self)
        about.setMenuRole(QAction.MenuRole.AboutRole)
        about.triggered.connect(self._about)
        help_menu.addAction(about)

        finding = QAction("Finding Your Camera's RTSP URL", self)
        finding.triggered.connect(self._rtsp_help)
        help_menu.addAction(finding)

        logs = QAction("Open App Data Folder", self)
        logs.triggered.connect(lambda: open_path(app_data_dir()))
        help_menu.addAction(logs)

    def _wire(self) -> None:
        self.live.addCameraRequested.connect(self.add_camera)
        self.live.editCameraRequested.connect(self.edit_camera)
        self.live.removeCameraRequested.connect(self.remove_camera)
        self.live.snapshotRequested.connect(self.save_live_snapshot)
        self.live.viewRecordingsRequested.connect(self.show_recordings_for)
        self.live.toggleRecordRequested.connect(self.toggle_camera_recording)

        self.cameras_view.addRequested.connect(self.add_camera)
        self.cameras_view.editRequested.connect(self.edit_camera)
        self.cameras_view.removeRequested.connect(self.remove_camera)
        self.cameras_view.reordered.connect(self.reorder_cameras)

        self.storage_view.applied.connect(self.apply_storage)
        self.storage_view.cleanupRequested.connect(lambda: self.run_retention(True))

    # -------------------------------------------------------------------- setup
    def run_wizard(self) -> None:
        wizard = SetupWizard(self)
        if wizard.exec() != QDialog.DialogCode.Accepted:
            self.config.onboarded = True
            self.config.save()
            return
        self.config.storage = wizard.storage
        for camera in wizard.cameras:
            camera.name = self.config.unique_name(camera.name)
            self.config.add_camera(camera)
        self.config.onboarded = True
        self.config.save()
        self.restart_sessions()
        self.refresh_all()
        self.run_retention(False)

    # ------------------------------------------------------------------ cameras
    def add_camera(self) -> None:
        dialog = CameraDialog(
            parent=self, existing_names={c.name for c in self.config.cameras})
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        camera = dialog.result_camera()
        camera.name = self.config.unique_name(camera.name)
        self.config.add_camera(camera)
        self.config.save()
        self.refresh_all()
        self.start_session(camera)
        self.tabs.setCurrentIndex(TAB_LIVE)

    def edit_camera(self, camera_id: str) -> None:
        camera = self.config.camera(camera_id)
        if camera is None:
            return
        others = {c.name for c in self.config.cameras if c.id != camera_id}
        dialog = CameraDialog(camera, parent=self, existing_names=others)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.config.replace_camera(dialog.result_camera())
        self.config.save()
        self.refresh_all()
        self.restart_session(camera_id)

    def remove_camera(self, camera_id: str) -> None:
        camera = self.config.camera(camera_id)
        if camera is None:
            return
        answer = QMessageBox.question(
            self, "Remove camera",
            f"Remove “{camera.name}” from SentryView?\n\n"
            "Recordings already on disk are kept — delete them yourself if you "
            "no longer want them.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.stop_session(camera_id)
        self.config.remove_camera(camera_id)
        self.config.save()
        self.refresh_all()

    def toggle_camera_recording(self, camera_id: str) -> None:
        camera = self.config.camera(camera_id)
        if camera is None:
            return
        camera.record = not camera.record
        self.config.save()
        self.refresh_all()
        self.restart_session(camera_id)

    def reorder_cameras(self, order: list) -> None:
        index = {camera_id: position for position, camera_id in enumerate(order)}
        self.config.cameras.sort(key=lambda c: index.get(c.id, 999))
        self.config.save()
        self.refresh_all()

    def save_live_snapshot(self, camera_id: str) -> None:
        image = self.live.tile_frame(camera_id)
        camera = self.config.camera(camera_id)
        if image is None or image.isNull() or camera is None:
            QMessageBox.information(self, "No frame yet",
                                    "Wait for the camera to show a picture, "
                                    "then try again.")
            return
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        suggested = str(Path.home() / f"{camera.folder_name()}-{stamp}.png")
        path, _ = QFileDialog.getSaveFileName(self, "Save snapshot", suggested,
                                              "PNG image (*.png)")
        if path:
            image.save(path, "PNG")

    def show_recordings_for(self, camera_id: str) -> None:
        camera = self.config.camera(camera_id)
        if camera is None:
            return
        self.tabs.setCurrentIndex(TAB_RECORDINGS)
        self.playback.reload()
        self.playback.select_camera_folder(camera.folder_name())

    # ------------------------------------------------------------------ storage
    def apply_storage(self, storage: Storage) -> None:
        if storage.enabled and storage.root:
            try:
                Path(storage.root).expanduser().mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                QMessageBox.warning(
                    self, "Folder unavailable",
                    f"SentryView cannot write to that folder:\n\n{exc}")
                return
        self.config.storage = storage
        self.config.save()
        self._sync_retention_interval()
        self.restart_sessions()
        self.refresh_all()
        self.run_retention(False)

    def _sync_retention_interval(self) -> None:
        """Sweep every minute while a size cap is in force, else every five."""
        storage = self.config.storage
        interval = (RETENTION_INTERVAL_CAPPED_MS
                    if (storage.enabled and storage.max_total_gb)
                    else RETENTION_INTERVAL_MS)
        if self.retention_timer.interval() != interval:
            self.retention_timer.start(interval)

    def run_retention(self, announce: bool) -> None:
        storage = self.config.storage
        self._last_sweep_at = time.monotonic()
        if not storage.enabled or not storage.root:
            if announce:
                QMessageBox.information(self, "Nothing to clean",
                                        "Recording is turned off.")
            return
        protect = {s.current_clip for s in self.sessions.values() if s.current_clip}
        manager = RetentionManager(storage.root, storage.retention_days,
                                   storage.max_total_gb)
        report = manager.sweep(protect=protect)
        if report.removed_files or report.removed_days:
            self._last_sweep = (f"{report.describe()} · "
                                f"{dt.datetime.now().strftime('%H:%M')}")
            if self.tabs.currentIndex() == TAB_RECORDINGS:
                self.playback.reload()
        elif announce:
            self._last_sweep = "Already within limits"
        self.storage_view.refresh_stats(self._last_sweep)
        if announce:
            message = report.describe()
            if report.errors:
                message += "\n\nSome files could not be removed:\n" + \
                           "\n".join(report.errors[:5])
            QMessageBox.information(self, "Storage cleanup", message)

    def _open_recordings_folder(self) -> None:
        root = self.config.storage.root
        if not root:
            QMessageBox.information(self, "No folder set",
                                    "Choose a recordings folder on the Storage "
                                    "page first.")
            return
        target = Path(root).expanduser()
        if not target.exists():
            QMessageBox.information(self, "Folder not created yet",
                                    "Nothing has been recorded to that folder yet.")
            return
        open_path(target)

    # ----------------------------------------------------------------- sessions
    def start_session(self, camera: Camera) -> None:
        if camera.id in self.sessions or not camera.enabled or not camera.url:
            return
        session = CameraSessionFactory.create(camera, self.config)
        session.frameReady.connect(self.live.on_frame)
        session.stateChanged.connect(self._on_session_state)
        session.clipStarted.connect(self._on_clip_started)
        self.sessions[camera.id] = session
        session.start()

    def stop_session(self, camera_id: str) -> None:
        session = self.sessions.pop(camera_id, None)
        if session is not None:
            session.stop()

    def restart_session(self, camera_id: str) -> None:
        self.stop_session(camera_id)
        camera = self.config.camera(camera_id)
        if camera is not None:
            self.start_session(camera)

    def start_sessions(self) -> None:
        for camera in self.config.cameras:
            self.start_session(camera)

    def stop_all_sessions(self) -> None:
        sessions = list(self.sessions.values())
        self.sessions.clear()
        for session in sessions:          # ask them all to wind down first,
            session.request_stop()        # then wait, so quitting stays quick
        for session in sessions:
            session.stop(wait_ms=12000)

    def restart_sessions(self) -> None:
        self.stop_all_sessions()
        self.start_sessions()

    def _on_session_state(self, camera_id: str, state: str, detail: str) -> None:
        self.live.on_state(camera_id, state, detail)
        self.cameras_view.set_state(camera_id, state, detail)
        session = self.sessions.get(camera_id)
        self.live.set_recording(camera_id,
                                bool(session and session.is_recording()))
        self._refresh_status()

    def _on_clip_started(self, camera_id: str, path: str) -> None:
        session = self.sessions.get(camera_id)
        self.live.set_recording(camera_id,
                                bool(session and session.is_recording()))
        # A clip rolling over is the moment new bytes have just landed, so it
        # is the right time to re-check a size cap rather than waiting out the
        # timer. Debounced so a wall of cameras rolling together sweeps once.
        storage = self.config.storage
        if (storage.enabled and storage.max_total_gb
                and time.monotonic() - self._last_sweep_at >= MIN_SWEEP_GAP_S):
            self.run_retention(False)

    # ------------------------------------------------------------------ refresh
    def refresh_all(self) -> None:
        cameras = self.config.cameras
        self.live.set_cameras(cameras)
        self.cameras_view.set_cameras(cameras)
        self.storage_view.load(self.config.storage)
        self.playback.configure(
            self.config.storage.root if self.config.storage.root else None, cameras)
        for camera_id, session in self.sessions.items():
            self.live.set_recording(camera_id, session.is_recording())
        self._refresh_status()

    def _on_tab_changed(self, index: int) -> None:
        if index == TAB_RECORDINGS:
            self.playback.reload()
        elif index == TAB_STORAGE:
            self.storage_view.refresh_stats(self._last_sweep)

    def _refresh_status(self) -> None:
        storage = self.config.storage
        total = len(self.config.cameras)
        live_count = sum(1 for s in self.sessions.values() if s.state == sess.LIVE)
        recording = sum(1 for s in self.sessions.values() if s.is_recording())

        if not total:
            self.record_status.setText("No cameras")
        elif storage.enabled and storage.root:
            colour = theme.REC if recording else theme.WARN
            self.record_status.setText(
                f'<span style="color:{colour}">●</span> Recording '
                f'{recording} of {total} · {live_count} live')
        else:
            self.record_status.setText(f"Live only · {live_count} of {total} "
                                       f"connected")

        if storage.enabled and storage.root:
            library = Library(storage.root)
            manager = RetentionManager(storage.root)
            used = library.total_bytes() if library.available() else 0
            free = manager.disk_free_bytes()
            self.storage_status.setText(
                f"{human_size(used)} recorded · {human_size(free)} free"
                + (f" · {self._last_sweep}" if self._last_sweep else ""))
            limits = []
            if storage.retention_days:
                limits.append(f"{storage.retention_days} day limit")
            if storage.max_total_gb:
                limits.append(f"{storage.max_total_gb:g} GB cap")
            self.policy_status.setText(" · ".join(limits) or "No limits set")
        else:
            self.storage_status.setText("Recording is off")
            self.policy_status.setText("")

    # -------------------------------------------------------------------- misc
    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _about(self) -> None:
        ffmpeg = find_ffmpeg() or "not found"
        QMessageBox.information(
            self, f"About {APP_NAME}",
            f"<b>{APP_NAME} {__version__}</b><br><br>"
            "A local RTSP camera viewer and recorder. Your video never leaves "
            "this computer.<br><br>"
            f"<b>Config:</b> {app_data_dir()}<br>"
            f"<b>ffmpeg:</b> {ffmpeg}<br><br>"
            "Video decoding and recording use FFmpeg (LGPL).")

    def _rtsp_help(self) -> None:
        QMessageBox.information(
            self, "Finding your camera's RTSP URL",
            "<b>Where to look</b><br>"
            "Most cameras list their RTSP address in the vendor app under "
            "Advanced, Network or Developer settings. Enabling RTSP (sometimes "
            "called ONVIF) may be required first.<br><br>"
            "<b>Common patterns</b><br>"
            "<tt>Hikvision &nbsp; rtsp://IP:554/Streaming/Channels/101</tt><br>"
            "<tt>Dahua &nbsp;&nbsp;&nbsp;&nbsp;&nbsp; rtsp://IP:554/cam/realmonitor"
            "?channel=1&amp;subtype=0</tt><br>"
            "<tt>Reolink &nbsp;&nbsp;&nbsp; rtsp://IP:554/h264Preview_01_main</tt><br>"
            "<tt>Amcrest &nbsp;&nbsp;&nbsp; rtsp://IP:554/cam/realmonitor"
            "?channel=1&amp;subtype=0</tt><br>"
            "<tt>UniFi &nbsp;&nbsp;&nbsp;&nbsp;&nbsp; rtsp://IP:7447/&lt;stream key&gt;"
            "</tt><br><br>"
            "Many cameras also offer a lower-resolution substream (often "
            "<tt>subtype=1</tt> or <tt>_sub</tt>), which is lighter to view and "
            "record.<br><br>"
            "<b>No camera to hand?</b><br>"
            "Run <tt>python3 tools/test_camera.py</tt> from the source folder and "
            "add <tt>rtsp://127.0.0.1:8554/cam1</tt>.")

    # ------------------------------------------------------------------ geometry
    def _restore_geometry(self) -> None:
        blob = self.config.ui.window_geometry
        if blob:
            try:
                self.restoreGeometry(QByteArray.fromBase64(blob.encode("ascii")))
            except Exception:
                pass

    def closeEvent(self, event) -> None:
        if self._shutting_down:
            event.accept()
            return
        self._shutting_down = True

        try:
            self.config.ui.window_geometry = bytes(
                self.saveGeometry().toBase64()).decode("ascii")
            self.config.save()
        except Exception:
            pass

        self.retention_timer.stop()
        self.status_timer.stop()
        self.statusBar().showMessage("Finishing recordings…")
        QWidget.repaint(self)

        self.playback.shutdown()
        self.stop_all_sessions()
        event.accept()


class CameraSessionFactory:
    """Builds a session from the current configuration."""

    @staticmethod
    def create(camera: Camera, config: Config):
        from ..session import CameraSession
        return CameraSession(camera, config.storage, config.ui.live_fps,
                             config.ui.tile_width)
