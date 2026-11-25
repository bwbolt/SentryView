"""The recordings browser: pick a camera and day, scrub, review, export."""
from __future__ import annotations

import datetime as dt
from collections import deque
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (QAction, QColor, QIcon, QImage, QKeySequence,
                           QPainter, QPixmap, QShortcut)
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog,
                               QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QMenu, QMessageBox, QPushButton, QSizePolicy, QSlider,
                               QSplitter, QToolButton, QVBoxLayout, QWidget)

from ..library import Clip, Library
from ..player import ENDED, PAUSED, PLAYING, SPEEDS, ClipPlayer, export_clip
from ..thumbs import thumbnail_for
from ..util import (friendly_day, human_duration, human_size, open_path,
                    reveal_in_file_manager)
from . import theme
from .widgets import DayTimeline, VideoSurface

PATH_ROLE = Qt.ItemDataRole.UserRole + 1


def _placeholder_icon(size: QSize) -> QIcon:
    """Keeps rows aligned until the real thumbnail is generated."""
    pixmap = QPixmap(size)
    pixmap.fill(QColor("#11131a"))
    painter = QPainter(pixmap)
    painter.setPen(QColor(theme.BORDER_SOFT))
    painter.drawRect(0, 0, size.width() - 1, size.height() - 1)
    painter.end()
    return QIcon(pixmap)


class ThumbLoader(QThread):
    """Generates clip thumbnails off the UI thread."""

    ready = Signal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._queue: deque = deque()
        self._stop = False

    def request(self, path: Path) -> None:
        self._queue.append(Path(path))

    def clear(self) -> None:
        self._queue.clear()

    def stop(self) -> None:
        self._stop = True
        self._queue.clear()
        self.wait(4000)

    def run(self) -> None:
        while not self._stop:
            if not self._queue:
                self.msleep(80)
                continue
            path = self._queue.popleft()
            try:
                thumb = thumbnail_for(path)
            except Exception:
                thumb = None
            if thumb and not self._stop:
                self.ready.emit(str(path), str(thumb))


class PlaybackView(QWidget):
    """Camera + day navigation on the left, player centre, clips on the right."""

    storageRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.library = Library(None)
        self.cameras: list = []
        self.folder_names: dict = {}          # folder -> display name
        self.clips: list = []
        self.current_clip: Clip | None = None
        self._duration = 0.0
        self._scrubbing = False

        self.player = ClipPlayer(self)
        self.player.frameReady.connect(self._on_frame)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.stateChanged.connect(self._on_player_state)
        self.player.start()

        self.thumbs = ThumbLoader(self)
        self.thumbs.ready.connect(self._on_thumb)
        self.thumbs.start()

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 16)
        root.setSpacing(10)
        root.addWidget(self._build_header())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_sidebar())
        splitter.addWidget(self._build_center())
        splitter.addWidget(self._build_clip_panel())
        splitter.setSizes([220, 720, 320])
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        self._install_shortcuts()
        self._set_controls_enabled(False)

    # ----------------------------------------------------------------- building
    def _build_header(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        title = QLabel("Recordings")
        title.setProperty("role", "title")
        row.addWidget(title)

        self.header_note = QLabel("")
        self.header_note.setProperty("role", "subtitle")
        row.addWidget(self.header_note)
        row.addStretch(1)

        self.continuous = QCheckBox("Play continuously")
        self.continuous.setChecked(True)
        self.continuous.setToolTip("When a clip ends, roll straight into the next one")
        row.addWidget(self.continuous)

        refresh = QPushButton("Refresh")
        refresh.setProperty("variant", "ghost")
        refresh.clicked.connect(self.reload)
        row.addWidget(refresh)

        self.open_folder_button = QPushButton("Open folder")
        self.open_folder_button.setProperty("variant", "ghost")
        self.open_folder_button.clicked.connect(self._open_root)
        row.addWidget(self.open_folder_button)
        return bar

    def _build_sidebar(self) -> QWidget:
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 8, 0)
        box.setSpacing(6)

        cameras_label = QLabel("Cameras")
        cameras_label.setProperty("role", "section")
        box.addWidget(cameras_label)
        self.camera_list = QListWidget()
        self.camera_list.setMaximumHeight(190)
        self.camera_list.currentItemChanged.connect(
            lambda *_: self._on_camera_selected())
        box.addWidget(self.camera_list)

        days_label = QLabel("Days")
        days_label.setProperty("role", "section")
        box.addWidget(days_label)
        self.day_list = QListWidget()
        self.day_list.currentItemChanged.connect(lambda *_: self._on_day_selected())
        box.addWidget(self.day_list, 1)
        return panel

    def _build_center(self) -> QWidget:
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(8, 0, 8, 0)
        box.setSpacing(8)

        self.surface = VideoSurface()
        self.surface.placeholder = "Select a clip to play"
        self.surface.setSizePolicy(QSizePolicy.Policy.Expanding,
                                  QSizePolicy.Policy.Expanding)
        self.surface.doubleClicked.connect(self._toggle_play)
        box.addWidget(self.surface, 1)

        box.addWidget(self._build_controls())

        timeline_label = QLabel("Day timeline — click to jump")
        timeline_label.setProperty("role", "hint")
        box.addWidget(timeline_label)
        self.timeline = DayTimeline()
        self.timeline.timeSelected.connect(self._jump_to_time)
        box.addWidget(self.timeline)
        return panel

    def _build_controls(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        def tool(text: str, tip: str, slot) -> QToolButton:
            button = QToolButton()
            button.setText(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            return button

        self.prev_button = tool("⏮", "Previous clip", self.previous_clip)
        self.back_button = tool("⏪", "Back 10 seconds", lambda: self._nudge(-10))
        self.step_back_button = tool("◀|", "Previous clip start",
                                     lambda: self._nudge(-1))
        self.play_button = tool("▶", "Play / pause (Space)", self._toggle_play)
        self.step_button = tool("|▶", "Step one frame (.)",
                                lambda: self.player.step_frames(1))
        self.forward_button = tool("⏩", "Forward 10 seconds", lambda: self._nudge(10))
        self.next_button = tool("⏭", "Next clip", self.next_clip)

        for button in (self.prev_button, self.back_button, self.step_back_button,
                       self.play_button, self.step_button, self.forward_button,
                       self.next_button):
            row.addWidget(button)

        self.position_label = QLabel("0:00")
        self.position_label.setProperty("role", "mono")
        self.position_label.setMinimumWidth(42)
        self.position_label.setAlignment(Qt.AlignmentFlag.AlignRight
                                         | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.position_label)

        self.scrub = QSlider(Qt.Orientation.Horizontal)
        self.scrub.setRange(0, 1000)
        self.scrub.sliderPressed.connect(self._scrub_start)
        self.scrub.sliderReleased.connect(self._scrub_end)
        self.scrub.sliderMoved.connect(self._scrub_move)
        row.addWidget(self.scrub, 1)

        self.duration_label = QLabel("0:00")
        self.duration_label.setProperty("role", "mono")
        self.duration_label.setMinimumWidth(42)
        row.addWidget(self.duration_label)

        self.speed_box = QComboBox()
        for speed in SPEEDS:
            self.speed_box.addItem(f"{speed:g}×", speed)
        self.speed_box.setCurrentIndex(SPEEDS.index(1.0))
        self.speed_box.setFixedWidth(74)
        self.speed_box.currentIndexChanged.connect(
            lambda i: self.player.set_speed(self.speed_box.itemData(i)))
        row.addWidget(self.speed_box)

        self.snapshot_button = QPushButton("Snapshot")
        self.snapshot_button.setProperty("variant", "ghost")
        self.snapshot_button.clicked.connect(self._save_snapshot)
        row.addWidget(self.snapshot_button)

        self.export_button = QPushButton("Export…")
        self.export_button.setProperty("variant", "ghost")
        self.export_button.clicked.connect(self._export_current)
        row.addWidget(self.export_button)
        return bar

    def _build_clip_panel(self) -> QWidget:
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(8, 0, 0, 0)
        box.setSpacing(6)

        self.clips_label = QLabel("Clips")
        self.clips_label.setProperty("role", "section")
        box.addWidget(self.clips_label)

        self.clip_list = QListWidget()
        self.clip_list.setIconSize(QSize(116, 66))
        self._blank_icon = _placeholder_icon(QSize(116, 66))
        self.clip_list.setUniformItemSizes(False)
        self.clip_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.clip_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.clip_list.customContextMenuRequested.connect(self._clip_menu)
        self.clip_list.currentItemChanged.connect(lambda *_: self._on_clip_selected())
        box.addWidget(self.clip_list, 1)

        self.clip_summary = QLabel("")
        self.clip_summary.setProperty("role", "hint")
        self.clip_summary.setWordWrap(True)
        box.addWidget(self.clip_summary)
        return panel

    def _install_shortcuts(self) -> None:
        bindings = [
            (QKeySequence(Qt.Key.Key_Space), self._toggle_play),
            (QKeySequence(Qt.Key.Key_Left), lambda: self._nudge(-5)),
            (QKeySequence(Qt.Key.Key_Right), lambda: self._nudge(5)),
            (QKeySequence(Qt.Key.Key_Period), lambda: self.player.step_frames(1)),
            (QKeySequence(Qt.Key.Key_Comma), lambda: self._nudge(-0.5)),
            (QKeySequence(Qt.Key.Key_BracketRight), self.next_clip),
            (QKeySequence(Qt.Key.Key_BracketLeft), self.previous_clip),
        ]
        for sequence, slot in bindings:
            shortcut = QShortcut(sequence, self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)

    # ------------------------------------------------------------------ loading
    def configure(self, root: str | None, cameras: list) -> None:
        """Point the browser at a recording root and the configured cameras."""
        self.library = Library(root)
        self.cameras = list(cameras)
        self.folder_names = {c.folder_name(): c.name for c in cameras}
        self.reload()

    def reload(self) -> None:
        previous = self._selected_folder()
        self.camera_list.blockSignals(True)
        self.camera_list.clear()

        folders = self.library.camera_folders()
        if not folders:
            self.camera_list.blockSignals(False)
            self.day_list.clear()
            self.clip_list.clear()
            self.timeline.set_clips([])
            self.clips = []
            self.current_clip = None
            self._set_controls_enabled(False)
            if not self.library.available():
                self.header_note.setText("Recording is off — no saved video yet")
                self.open_folder_button.setEnabled(False)
            else:
                self.header_note.setText("No recordings found yet")
                self.open_folder_button.setEnabled(True)
            self.surface.placeholder = "No recordings available"
            self.surface.clear_frame()
            return

        self.open_folder_button.setEnabled(True)
        self.header_note.setText("")
        for folder in folders:
            label = self.folder_names.get(folder) or self._prettify(folder)
            item = QListWidgetItem(label)
            item.setData(PATH_ROLE, folder)
            if folder not in self.folder_names:
                item.setToolTip(f"{folder}\n(camera no longer configured)")
                item.setForeground(Qt.GlobalColor.gray)
            self.camera_list.addItem(item)
        self.camera_list.blockSignals(False)

        target = 0
        if previous:
            for index in range(self.camera_list.count()):
                if self.camera_list.item(index).data(PATH_ROLE) == previous:
                    target = index
                    break
        self.camera_list.setCurrentRow(target)

    @staticmethod
    def _prettify(folder: str) -> str:
        base = folder.rsplit("_", 1)[0]
        return base.replace("_", " ") or folder

    def _selected_folder(self) -> str | None:
        item = self.camera_list.currentItem()
        return item.data(PATH_ROLE) if item else None

    def _selected_day(self) -> dt.date | None:
        item = self.day_list.currentItem()
        return item.data(PATH_ROLE) if item else None

    def _on_camera_selected(self) -> None:
        folder = self._selected_folder()
        self.day_list.blockSignals(True)
        self.day_list.clear()
        if folder:
            for day in self.library.days(folder):
                count, size = self.library.day_summary(folder, day)
                if not count:
                    continue
                item = QListWidgetItem(
                    f"{friendly_day(day)}\n{count} clips · {human_size(size)}")
                item.setData(PATH_ROLE, day)
                self.day_list.addItem(item)
        self.day_list.blockSignals(False)
        if self.day_list.count():
            self.day_list.setCurrentRow(0)
        else:
            self.clip_list.clear()
            self.clips = []
            self.timeline.set_clips([])
            self._set_controls_enabled(False)

    def _on_day_selected(self) -> None:
        folder = self._selected_folder()
        day = self._selected_day()
        self.thumbs.clear()
        self.clip_list.blockSignals(True)
        self.clip_list.clear()
        self.clips = []

        if folder and day:
            self.clips = self.library.clips(folder, day)
            for clip in self.clips:
                item = QListWidgetItem(
                    f"{clip.label()}\n"
                    f"{human_duration(clip.duration)} · {human_size(clip.size)}")
                item.setData(PATH_ROLE, str(clip.path))
                item.setToolTip(
                    f"{clip.start.strftime('%H:%M:%S')} – "
                    f"{clip.end.strftime('%H:%M:%S')}\n{clip.path.name}")
                item.setSizeHint(QSize(0, 78))
                item.setIcon(self._blank_icon)
                self.clip_list.addItem(item)
                self.thumbs.request(clip.path)

        self.clip_list.blockSignals(False)
        self.timeline.set_clips(self.clips)
        total = sum(c.size for c in self.clips)
        covered = sum(c.duration for c in self.clips)
        self.clips_label.setText(f"Clips · {len(self.clips)}")
        self.clip_summary.setText(
            f"{human_size(total)} · {human_duration(covered)} of footage"
            if self.clips else "No clips for this day")
        if self.clips:
            self.clip_list.setCurrentRow(0)
        else:
            self._set_controls_enabled(False)

    def _on_thumb(self, clip_path: str, thumb_path: str) -> None:
        for index in range(self.clip_list.count()):
            item = self.clip_list.item(index)
            if item.data(PATH_ROLE) == clip_path:
                item.setIcon(QIcon(thumb_path))
                return

    # ---------------------------------------------------------------- selection
    def _on_clip_selected(self) -> None:
        item = self.clip_list.currentItem()
        if item is None:
            return
        path = item.data(PATH_ROLE)
        clip = next((c for c in self.clips if str(c.path) == path), None)
        if clip is None:
            return
        self.current_clip = clip
        self.surface.placeholder = "Loading…"
        self._set_controls_enabled(True)
        self.player.load(clip.path)
        self._update_timeline_playhead(0.0)

    def select_camera_folder(self, folder: str) -> bool:
        for index in range(self.camera_list.count()):
            if self.camera_list.item(index).data(PATH_ROLE) == folder:
                self.camera_list.setCurrentRow(index)
                return True
        return False

    # ------------------------------------------------------------------ playing
    def _set_controls_enabled(self, enabled: bool) -> None:
        for widget in (self.prev_button, self.back_button, self.step_back_button,
                       self.play_button, self.step_button, self.forward_button,
                       self.next_button, self.scrub, self.speed_box,
                       self.snapshot_button, self.export_button):
            widget.setEnabled(enabled)

    def _toggle_play(self) -> None:
        if self.current_clip is not None:
            self.player.toggle()

    def _nudge(self, seconds: float) -> None:
        if self.current_clip is None:
            return
        target = self.player.position() + seconds
        if target < 0:
            if self.continuous.isChecked() and self._clip_index() > 0:
                self.previous_clip(from_end=True)
                return
            target = 0.0
        self.player.seek(target)

    def _clip_index(self) -> int:
        if self.current_clip is None:
            return -1
        for index, clip in enumerate(self.clips):
            if clip.path == self.current_clip.path:
                return index
        return -1

    def next_clip(self) -> bool:
        index = self._clip_index()
        if index < 0 or index + 1 >= len(self.clips):
            return False
        self.clip_list.setCurrentRow(index + 1)
        return True

    def previous_clip(self, from_end: bool = False) -> bool:
        index = self._clip_index()
        if index <= 0:
            return False
        self.clip_list.setCurrentRow(index - 1)
        if from_end:
            QTimer.singleShot(350, lambda: self.player.seek(
                max(0.0, self.player.duration() - 3.0)))
        return True

    def _jump_to_time(self, seconds: float) -> None:
        """Map a click on the 24-hour bar to a clip plus an offset inside it."""
        if not self.clips:
            return
        day = self.clips[0].day
        target = dt.datetime.combine(day, dt.time()) + dt.timedelta(seconds=seconds)

        chosen = None
        for clip in self.clips:
            if clip.start <= target < clip.start + dt.timedelta(
                    seconds=max(1.0, clip.duration)):
                chosen = clip
                break
        if chosen is None:
            chosen = min(self.clips,
                         key=lambda c: abs((c.start - target).total_seconds()))
            offset = 0.0
        else:
            offset = (target - chosen.start).total_seconds()

        index = next((i for i, c in enumerate(self.clips)
                      if c.path == chosen.path), 0)
        if self.current_clip and self.current_clip.path == chosen.path:
            self.player.seek(offset)
        else:
            self.clip_list.setCurrentRow(index)
            QTimer.singleShot(400, lambda: self.player.seek(offset))

    # ------------------------------------------------------------- player signals
    def _on_frame(self, image: QImage) -> None:
        self.surface.set_frame(image)

    def _on_duration(self, duration: float) -> None:
        self._duration = duration
        self.duration_label.setText(human_duration(duration))

    def _on_position(self, position: float) -> None:
        if self._scrubbing:
            return
        self.position_label.setText(human_duration(position))
        if self._duration > 0:
            self.scrub.setValue(int(1000 * min(1.0, position / self._duration)))
        self._update_timeline_playhead(position)

    def _update_timeline_playhead(self, position: float) -> None:
        if self.current_clip is None:
            self.timeline.set_playhead(None)
            return
        start = self.current_clip.start
        self.timeline.set_playhead(
            start.hour * 3600 + start.minute * 60 + start.second + position)

    def _on_player_state(self, state: str) -> None:
        self.play_button.setText("⏸" if state == PLAYING else "▶")
        if state == PLAYING:
            self.surface.placeholder = ""
        if state == ENDED and self.continuous.isChecked():
            if self.next_clip():
                QTimer.singleShot(420, self.player.play)

    def _scrub_start(self) -> None:
        self._scrubbing = True

    def _scrub_move(self, value: int) -> None:
        if self._duration > 0:
            self.position_label.setText(human_duration(value / 1000 * self._duration))

    def _scrub_end(self) -> None:
        self._scrubbing = False
        if self._duration > 0:
            self.player.seek(self.scrub.value() / 1000 * self._duration)

    # ------------------------------------------------------------------- actions
    def _open_root(self) -> None:
        if self.library.root and self.library.root.exists():
            open_path(self.library.root)

    def _save_snapshot(self) -> None:
        image = self.surface.current_frame()
        if image is None or image.isNull() or self.current_clip is None:
            return
        stamp = self.current_clip.start.strftime("%Y%m%d-%H%M%S")
        suggested = str(Path.home() / f"snapshot-{stamp}.png")
        path, _ = QFileDialog.getSaveFileName(self, "Save snapshot", suggested,
                                              "PNG image (*.png)")
        if path:
            image.save(path, "PNG")

    def _export_current(self) -> None:
        if self.current_clip is None:
            return
        suggested = str(Path.home() / self.current_clip.path.name)
        path, _ = QFileDialog.getSaveFileName(self, "Export clip", suggested,
                                              "MP4 video (*.mp4)")
        if not path:
            return
        ok, detail = export_clip(self.current_clip.path, Path(path))
        if ok:
            QMessageBox.information(self, "Export complete",
                                    f"Saved to:\n{path}")
        else:
            QMessageBox.warning(self, "Export failed", detail)

    def _clip_menu(self, position) -> None:
        item = self.clip_list.itemAt(position)
        if item is None:
            return
        path = Path(item.data(PATH_ROLE))
        menu = QMenu(self)

        play = QAction("Play", menu)
        play.triggered.connect(lambda: (self.clip_list.setCurrentItem(item),
                                        self.player.play()))
        menu.addAction(play)

        external = QAction("Open in default player", menu)
        external.triggered.connect(lambda: open_path(path))
        menu.addAction(external)

        reveal = QAction("Reveal in file manager", menu)
        reveal.triggered.connect(lambda: reveal_in_file_manager(path))
        menu.addAction(reveal)
        menu.addSeparator()

        export = QAction("Export a copy…", menu)
        export.triggered.connect(lambda: self._export_path(path))
        menu.addAction(export)

        delete = QAction("Delete this clip", menu)
        delete.triggered.connect(lambda: self._delete_clip(path))
        menu.addAction(delete)
        menu.exec(self.clip_list.mapToGlobal(position))

    def _export_path(self, path: Path) -> None:
        target, _ = QFileDialog.getSaveFileName(
            self, "Export clip", str(Path.home() / path.name), "MP4 video (*.mp4)")
        if not target:
            return
        ok, detail = export_clip(path, Path(target))
        if ok:
            QMessageBox.information(self, "Export complete", f"Saved to:\n{target}")
        else:
            QMessageBox.warning(self, "Export failed", detail)

    def _delete_clip(self, path: Path) -> None:
        answer = QMessageBox.question(
            self, "Delete clip",
            f"Permanently delete this clip?\n\n{path.name}\n\n"
            "This cannot be undone.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.current_clip is not None and self.current_clip.path == path:
            self.player.load(Path(""))
            self.current_clip = None
            self.surface.clear_frame()
        try:
            path.unlink()
        except OSError as exc:
            QMessageBox.warning(self, "Could not delete", str(exc))
            return
        self._on_day_selected()

    def shutdown(self) -> None:
        self.thumbs.stop()
        self.player.close()
