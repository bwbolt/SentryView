#!/usr/bin/env python3
"""Render the SentryView icon at every size the installers need."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPointF, QRectF, Qt                      # noqa: E402
from PySide6.QtGui import (QBrush, QColor, QImage, QLinearGradient,  # noqa: E402
                           QPainter, QPainterPath, QPen, QRadialGradient)
from PySide6.QtWidgets import QApplication                           # noqa: E402

ASSETS = Path(__file__).resolve().parent.parent / "assets"
SIZES = [16, 32, 48, 64, 128, 256, 512, 1024]


def render(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    s = size / 1024.0
    body = QRectF(0, 0, size, size)

    # Rounded-square backdrop with a cool-to-deep gradient.
    plate = QPainterPath()
    plate.addRoundedRect(body.adjusted(size * 0.04, size * 0.04,
                                       -size * 0.04, -size * 0.04),
                         size * 0.22, size * 0.22)
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, QColor("#30405c"))
    gradient.setColorAt(1.0, QColor("#141922"))
    painter.fillPath(plate, QBrush(gradient))

    painter.setPen(QPen(QColor(255, 255, 255, 36), max(1.0, 6 * s)))
    painter.drawPath(plate)

    # Lens: concentric rings plus a highlight, reading as a camera at 16px too.
    centre = QPointF(size * 0.5, size * 0.47)
    outer_r = size * 0.255

    glow = QRadialGradient(centre, outer_r * 1.5)
    glow.setColorAt(0.0, QColor(76, 141, 255, 90))
    glow.setColorAt(1.0, QColor(76, 141, 255, 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(glow))
    painter.drawEllipse(centre, outer_r * 1.5, outer_r * 1.5)

    painter.setBrush(QColor("#0d1016"))
    painter.setPen(QPen(QColor("#4c8dff"), max(1.5, 26 * s)))
    painter.drawEllipse(centre, outer_r, outer_r)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#1b2535"))
    painter.drawEllipse(centre, outer_r * 0.62, outer_r * 0.62)

    painter.setBrush(QColor("#4c8dff"))
    painter.drawEllipse(centre, outer_r * 0.3, outer_r * 0.3)

    if size >= 64:
        painter.setBrush(QColor(255, 255, 255, 215))
        painter.drawEllipse(
            QPointF(centre.x() - outer_r * 0.3, centre.y() - outer_r * 0.34),
            outer_r * 0.13, outer_r * 0.13)

    # Recording bar along the base.
    if size >= 32:
        bar_h = size * 0.055
        bar = QRectF(size * 0.3, size * 0.805, size * 0.4, bar_h)
        path = QPainterPath()
        path.addRoundedRect(bar, bar_h / 2, bar_h / 2)
        painter.fillPath(path, QColor(255, 255, 255, 38))
        dot_r = bar_h * 0.72
        painter.setBrush(QColor("#ff4d4f"))
        painter.drawEllipse(QPointF(bar.left() + dot_r, bar.center().y()),
                            dot_r, dot_r)

    painter.end()
    return image


def write_ico(images: list, target: Path) -> None:
    """Write a PNG-compressed ICO (Vista and newer read these natively)."""
    from PySide6.QtCore import QBuffer, QByteArray

    blobs = []
    for image in images:
        store = QByteArray()
        buffer = QBuffer(store)
        buffer.open(QBuffer.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        buffer.close()
        blobs.append(bytes(store))

    count = len(blobs)
    header = b"\x00\x00\x01\x00" + count.to_bytes(2, "little")
    offset = 6 + 16 * count
    directory, payload = b"", b""
    for image, blob in zip(images, blobs):
        width = 0 if image.width() >= 256 else image.width()
        height = 0 if image.height() >= 256 else image.height()
        directory += bytes([width, height, 0, 0]) + (1).to_bytes(2, "little") + \
            (32).to_bytes(2, "little") + len(blob).to_bytes(4, "little") + \
            offset.to_bytes(4, "little")
        payload += blob
        offset += len(blob)
    target.write_bytes(header + directory + payload)


def render_check(size: int) -> QImage:
    """A white tick sized for the checkbox indicator."""
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor("#ffffff"))
    pen.setWidthF(size * 0.16)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    path = QPainterPath()
    path.moveTo(size * 0.24, size * 0.53)
    path.lineTo(size * 0.43, size * 0.72)
    path.lineTo(size * 0.77, size * 0.30)
    painter.drawPath(path)
    painter.end()
    return image


def render_dot(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#ffffff"))
    painter.drawEllipse(QPointF(size / 2, size / 2), size * 0.21, size * 0.21)
    painter.end()
    return image


def main() -> int:
    QApplication([])
    ASSETS.mkdir(parents=True, exist_ok=True)

    images = {}
    for size in SIZES:
        image = render(size)
        images[size] = image
        image.save(str(ASSETS / f"icon_{size}.png"), "PNG")

    images[512].save(str(ASSETS / "icon.png"), "PNG")

    for name, renderer in (("check", render_check), ("dot", render_dot)):
        renderer(17).save(str(ASSETS / f"{name}.png"), "PNG")
        renderer(34).save(str(ASSETS / f"{name}@2x.png"), "PNG")
    write_ico([images[s] for s in (16, 32, 48, 64, 128, 256)],
              ASSETS / "icon.ico")
    print(f"wrote icons to {ASSETS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
