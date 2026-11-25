"""Reusable widgets: video surface, camera tile, day timeline."""
from __future__ import annotations

import datetime as dt

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QPainter, QPainterPath,
                           QPen, QPixmap)
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .. import session as sess
from ..util import human_duration
from . import theme


class VideoSurface(QWidget):
    """Paints the most recent frame, letterboxed to preserve aspect ratio."""

    clicked = Signal()
    doubleClicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image: QImage | None = None
        self.placeholder = "No signal"
        self.setMinimumSize(160, 90)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def set_frame(self, image: QImage) -> None:
        self._image = image
        self.update()

    def clear_frame(self) -> None:
        self._image = None
        self.update()

    def current_frame(self) -> QImage | None:
        return self._image

    def video_rect(self) -> QRect:
        """Where the frame is actually drawn inside the widget."""
        if self._image is None or self._image.isNull():
            return self.rect()
        target = self.rect()
        scaled = self._image.size().scaled(target.size(),
                                           Qt.AspectRatioMode.KeepAspectRatio)
        return QRect(
            target.x() + (target.width() - scaled.width()) // 2,
            target.y() + (target.height() - scaled.height()) // 2,
            scaled.width(), scaled.height(),
        )

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0b0d11"))
        if self._image is not None and not self._image.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawImage(self.video_rect(), self._image)
        else:
            painter.setPen(QColor(theme.FAINT))
            font = painter.font()
            font.setPointSize(12)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.placeholder)
        self.paint_overlay(painter)
        painter.end()

    def paint_overlay(self, painter: QPainter) -> None:
        """Hook for subclasses."""

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit()
        super().mouseDoubleClickEvent(event)


class CameraTile(VideoSurface):
    """A live camera cell with name, recording indicator and status."""

    requestMaximize = Signal(str)
    requestEdit = Signal(str)
    requestMenu = Signal(str, QPoint)

    def __init__(self, camera_id: str, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.camera_id = camera_id
        self.name = name
        self.state = sess.CONNECTING
        self.detail = ""
        self.recording = False
        self.fps = 0.0
        self.selected = False
        self.placeholder = "Connecting…"
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.doubleClicked.connect(lambda: self.requestMaximize.emit(self.camera_id))

    def set_state(self, state: str, detail: str = "") -> None:
        self.state = state
        self.detail = detail
        if state == sess.LIVE:
            self.placeholder = ""
        elif state == sess.CONNECTING:
            self.placeholder = "Connecting…"
            self.clear_frame()
        elif state == sess.RECONNECTING:
            self.placeholder = "Reconnecting…"
        elif state == sess.DISABLED:
            self.placeholder = "Disabled"
            self.clear_frame()
        elif state == sess.STOPPED:
            self.placeholder = "Stopped"
        else:
            self.placeholder = "Unavailable"
        self.update()

    # ------------------------------------------------------------------ painting
    def paint_overlay(self, painter: QPainter) -> None:
        rect = self.rect()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Top gradient scrim so white text stays readable over bright scenes.
        scrim = QRect(rect.x(), rect.y(), rect.width(), 34)
        painter.fillRect(scrim, QColor(0, 0, 0, 110))

        name_font = QFont(painter.font())
        name_font.setPointSize(11)
        name_font.setBold(True)
        painter.setFont(name_font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(QRect(rect.x() + 10, rect.y(), rect.width() - 90, 34),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         self.name)

        if self.recording:
            dot = QRect(rect.right() - 58, rect.y() + 12, 9, 9)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(theme.REC)))
            painter.drawEllipse(dot)
            small = QFont(painter.font())
            small.setPointSize(9)
            small.setBold(True)
            painter.setFont(small)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(QRect(rect.right() - 44, rect.y(), 36, 34),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             "REC")

        # Error detail along the bottom when the stream is unhealthy.
        if self.state in (sess.RECONNECTING, sess.ERROR) and self.detail:
            band = QRect(rect.x(), rect.bottom() - 26, rect.width(), 26)
            painter.fillRect(band, QColor(0, 0, 0, 150))
            small = QFont(painter.font())
            small.setPointSize(9)
            small.setBold(False)
            painter.setFont(small)
            painter.setPen(QColor(theme.WARN))
            painter.drawText(band.adjusted(10, 0, -10, 0),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             painter.fontMetrics().elidedText(
                                 self.detail, Qt.TextElideMode.ElideRight,
                                 band.width() - 20))

        border = QColor(theme.ACCENT) if self.selected else QColor("#000000")
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(border, 2 if self.selected else 1))
        painter.drawRect(rect.adjusted(1, 1, -1, -1))

    def contextMenuEvent(self, event) -> None:
        self.requestMenu.emit(self.camera_id, event.globalPos())


class DayTimeline(QWidget):
    """A 24-hour coverage bar for one camera-day. Click to jump to a time."""

    timeSelected = Signal(float)         # seconds since midnight

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(54)
        self._spans: list = []           # (start_s, end_s)
        self._playhead: float | None = None
        self._hover: float | None = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_clips(self, clips: list) -> None:
        self._spans = []
        for clip in clips:
            start = (clip.start.hour * 3600 + clip.start.minute * 60
                     + clip.start.second)
            end = min(86400.0, start + max(1.0, clip.duration))
            self._spans.append((start, end))
        self.update()

    def set_playhead(self, seconds: float | None) -> None:
        self._playhead = seconds
        self.update()

    def _bar_rect(self) -> QRect:
        return QRect(8, 8, max(1, self.width() - 16), 22)

    def _x_to_seconds(self, x: int) -> float:
        bar = self._bar_rect()
        ratio = (x - bar.x()) / max(1, bar.width())
        return max(0.0, min(86399.0, ratio * 86400.0))

    def _seconds_to_x(self, seconds: float) -> float:
        bar = self._bar_rect()
        return bar.x() + (seconds / 86400.0) * bar.width()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(theme.BG))
        bar = self._bar_rect()

        path = QPainterPath()
        path.addRoundedRect(QRectF(bar), 5, 5)
        painter.fillPath(path, QColor("#171a21"))

        painter.save()
        painter.setClipPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.ACCENT_DIM))
        for start, end in self._spans:
            x1 = self._seconds_to_x(start)
            x2 = self._seconds_to_x(end)
            painter.drawRect(QRectF(x1, bar.y(), max(1.5, x2 - x1), bar.height()))
        painter.restore()

        # Hour ticks and labels.
        tick_font = QFont(painter.font())
        tick_font.setPointSize(8)
        painter.setFont(tick_font)
        for hour in range(0, 25, 2):
            x = self._seconds_to_x(hour * 3600)
            painter.setPen(QPen(QColor("#323a49"), 1))
            painter.drawLine(int(x), bar.bottom() + 2, int(x), bar.bottom() + 6)
            painter.setPen(QColor(theme.FAINT))
            label = f"{hour:02d}" if hour < 24 else "24"
            painter.drawText(QRect(int(x) - 12, bar.bottom() + 7, 24, 14),
                             Qt.AlignmentFlag.AlignCenter, label)

        painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        if self._hover is not None:
            x = self._seconds_to_x(self._hover)
            painter.setPen(QPen(QColor("#6b7483"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(int(x), bar.y(), int(x), bar.bottom())

        if self._playhead is not None:
            x = self._seconds_to_x(self._playhead)
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.drawLine(int(x), bar.y() - 3, int(x), bar.bottom() + 3)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.timeSelected.emit(self._x_to_seconds(event.position().x()))

    def mouseMoveEvent(self, event) -> None:
        self._hover = self._x_to_seconds(event.position().x())
        seconds = int(self._hover)
        self.setToolTip(f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}")
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = None
        self.update()


class StatCard(QFrame):
    """Label-over-value card used on the storage page."""

    def __init__(self, caption: str, value: str = "—",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(3)
        self.caption = QLabel(caption)
        self.caption.setProperty("role", "section")
        self.value = QLabel(value)
        self.value.setProperty("role", "stat")
        self.detail = QLabel("")
        self.detail.setProperty("role", "hint")
        layout.addWidget(self.caption)
        layout.addWidget(self.value)
        layout.addWidget(self.detail)

    def set_value(self, value: str, detail: str = "") -> None:
        self.value.setText(value)
        self.detail.setText(detail)


def thumbnail_pixmap(path, width: int = 150) -> QPixmap:
    pix = QPixmap(str(path))
    if pix.isNull():
        return QPixmap()
    return pix.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
