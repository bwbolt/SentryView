"""Application bootstrap."""
from __future__ import annotations

import signal
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from . import APP_NAME, __version__
from .config import Config
from .paths import find_ffmpeg, resource_dir
from .ui.main_window import MainWindow
from .ui.theme import default_font, stylesheet


def _app_icon() -> QIcon:
    for name in ("icon.png", "icon.ico", "icon.icns"):
        candidate = resource_dir() / "assets" / name
        if candidate.exists():
            return QIcon(str(candidate))
    return QIcon()


def _install_signal_handlers(app, window) -> QTimer:
    """Finish recordings cleanly when the process is asked to quit.

    Logging out, a system shutdown or a plain `kill` all arrive as SIGTERM.
    Without this the ffmpeg children die mid-fragment and the final couple of
    seconds of every clip are lost. The idle timer exists so the interpreter
    gets a chance to run the handler while Qt is in its event loop.
    """
    def handler(signum, frame):     # noqa: ARG001
        window.close()
        app.quit()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError, AttributeError):
            pass

    waker = QTimer()
    waker.timeout.connect(lambda: None)
    waker.start(400)
    return waker


def main(argv: list | None = None) -> int:
    QApplication.setApplicationName(APP_NAME)
    QApplication.setApplicationDisplayName(APP_NAME)
    QApplication.setOrganizationName(APP_NAME)
    QApplication.setApplicationVersion(__version__)

    app = QApplication(argv if argv is not None else sys.argv)
    app.setStyle("Fusion")
    app.setFont(default_font())
    app.setStyleSheet(stylesheet())
    icon = _app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    if not find_ffmpeg():
        QMessageBox.critical(
            None, f"{APP_NAME} cannot start",
            "The bundled FFmpeg binary could not be found, so no camera can be "
            "opened.\n\nIf you are running from source, install it with:\n"
            "    pip install imageio-ffmpeg\n\nOtherwise reinstall the app.")
        return 1

    config = Config.load()
    window = MainWindow(config)
    window.show()

    if not config.onboarded:
        window.run_wizard()

    waker = _install_signal_handlers(app, window)
    window.start_sessions()
    window.run_retention(False)
    try:
        return app.exec()
    finally:
        waker.stop()
