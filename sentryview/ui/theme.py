"""Dark theme shared by every view."""
from __future__ import annotations

import sys
from pathlib import Path

BG = "#14161b"
PANEL = "#1b1e25"
ELEVATED = "#232832"
HOVER = "#2b3140"
BORDER = "#2e3542"
BORDER_SOFT = "#262c37"
TEXT = "#e7eaf0"
MUTED = "#98a2b3"
FAINT = "#6b7483"
ACCENT = "#4c8dff"
ACCENT_DIM = "#2f6ad9"
REC = "#ff4d4f"
OK = "#36cf8b"
WARN = "#f0a63c"
BAD = "#ff6b6b"


_MONO_CANDIDATES = ["SF Mono", "Menlo", "Monaco", "Cascadia Mono", "Consolas",
                    "DejaVu Sans Mono", "Liberation Mono", "Courier New"]

_FONT_CANDIDATES = {
    "darwin": ["SF Pro Text", "Helvetica Neue", "Helvetica", "Arial"],
    "win32": ["Segoe UI Variable Text", "Segoe UI", "Tahoma", "Arial"],
    "linux": ["Inter", "Noto Sans", "DejaVu Sans", "Liberation Sans", "Arial"],
}


def _first_installed(candidates: list) -> str:
    """The first candidate family actually installed.

    Naming a missing family makes Qt walk its whole alias table on every
    lookup, which is slow and noisy, so resolve it once up front.
    """
    try:
        from PySide6.QtGui import QFontDatabase

        installed = set(QFontDatabase.families())
        for family in candidates:
            if family in installed:
                return f'"{family}"'
    except Exception:
        pass
    return ", ".join(f'"{family}"' for family in candidates)


def ui_font() -> str:
    key = sys.platform if sys.platform in _FONT_CANDIDATES else "linux"
    return _first_installed(_FONT_CANDIDATES[key])


def mono_font() -> str:
    return _first_installed(_MONO_CANDIDATES)


def _asset_url(name: str) -> str:
    """Absolute, forward-slashed URL for a stylesheet image."""
    from ..paths import resource_dir

    candidate = resource_dir() / "assets" / name
    return candidate.as_posix() if candidate.exists() else ""


def default_font():
    """A concrete QFont, so Qt never has to resolve its generic fallback."""
    from PySide6.QtGui import QFont

    family = ui_font().split(",")[0].strip().strip('"')
    font = QFont(family)
    font.setPointSize(13)
    return font


def stylesheet() -> str:
    mono = mono_font()
    check = _asset_url("check.png")
    dot = _asset_url("dot.png")
    check_rule = f"image: url({check});" if check else ""
    dot_rule = f"image: url({dot});" if dot else ""
    return f"""
    * {{ font-family: {ui_font()}; }}

    QWidget {{ background: {BG}; color: {TEXT}; font-size: 13px; }}
    QMainWindow, QDialog {{ background: {BG}; }}

    QLabel {{ background: transparent; }}
    QLabel[role="title"] {{ font-size: 19px; font-weight: 600; }}
    QLabel[role="subtitle"] {{ font-size: 13px; color: {MUTED}; }}
    QLabel[role="section"] {{ font-size: 11px; font-weight: 700; color: {FAINT};
                              text-transform: uppercase; letter-spacing: 1px; }}
    QLabel[role="hint"] {{ font-size: 11px; color: {FAINT}; }}
    QLabel[role="stat"] {{ font-size: 17px; font-weight: 600; }}
    QLabel[role="mono"] {{ font-family: {mono}; font-size: 11px; color: {MUTED}; }}

    QFrame[role="card"] {{ background: {PANEL}; border: 1px solid {BORDER_SOFT};
                           border-radius: 10px; }}
    QFrame[role="sep"] {{ background: {BORDER_SOFT}; max-height: 1px; border: none; }}

    QPushButton {{
        background: {ELEVATED}; color: {TEXT}; border: 1px solid {BORDER};
        border-radius: 7px; padding: 7px 14px; font-size: 13px;
    }}
    QPushButton:hover {{ background: {HOVER}; border-color: #3b4454; }}
    QPushButton:pressed {{ background: #1d222c; }}
    QPushButton:disabled {{ color: {FAINT}; background: #1a1d24; border-color: {BORDER_SOFT}; }}
    QPushButton[variant="primary"] {{ background: {ACCENT}; border-color: {ACCENT};
                                      color: #ffffff; font-weight: 600; }}
    QPushButton[variant="primary"]:hover {{ background: #5e99ff; border-color: #5e99ff; }}
    QPushButton[variant="primary"]:pressed {{ background: {ACCENT_DIM}; }}
    QPushButton[variant="primary"]:disabled {{ background: #1f2531;
        border-color: {BORDER_SOFT}; color: {FAINT}; }}
    QPushButton[variant="danger"] {{ color: {BAD}; border-color: #46313a; }}
    QPushButton[variant="danger"]:disabled {{ color: {FAINT};
        border-color: {BORDER_SOFT}; background: #1a1d24; }}
    QPushButton[variant="ghost"]:disabled {{ color: #4c535f; background: transparent;
        border-color: transparent; }}
    QPushButton[variant="danger"]:hover {{ background: #33222a; }}
    QPushButton[variant="ghost"] {{ background: transparent; border-color: transparent;
                                    color: {MUTED}; }}
    QPushButton[variant="ghost"]:hover {{ background: {ELEVATED}; color: {TEXT}; }}
    QPushButton:checked {{ background: {ACCENT_DIM}; border-color: {ACCENT_DIM};
                           color: #ffffff; }}

    QToolButton {{ background: transparent; border: none; border-radius: 6px;
                   padding: 6px; color: {TEXT}; font-size: 15px; }}
    QToolButton:hover {{ background: {ELEVATED}; }}
    QToolButton:pressed {{ background: {HOVER}; }}
    QToolButton:disabled {{ color: {FAINT}; }}

    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {{
        background: #11131a; color: {TEXT}; border: 1px solid {BORDER};
        border-radius: 7px; padding: 7px 9px; selection-background-color: {ACCENT_DIM};
    }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
        border-color: {ACCENT};
    }}
    QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{
        color: {FAINT}; background: #15171d;
    }}
    QLineEdit[state="error"] {{ border-color: {BAD}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{
        background: {ELEVATED}; border: 1px solid {BORDER};
        selection-background-color: {ACCENT_DIM}; outline: none; padding: 4px;
    }}

    QCheckBox, QRadioButton {{ spacing: 9px; background: transparent; }}
    QCheckBox::indicator, QRadioButton::indicator {{ width: 17px; height: 17px; }}
    QCheckBox::indicator {{ border: 1px solid {BORDER}; border-radius: 5px;
                            background: #11131a; }}
    QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT};
                                    {check_rule} }}
    QCheckBox::indicator:disabled {{ background: #15171d; border-color: {BORDER_SOFT}; }}
    QCheckBox:disabled, QRadioButton:disabled {{ color: {FAINT}; }}
    QRadioButton::indicator {{ border: 1px solid {BORDER}; border-radius: 9px;
                               background: #11131a; }}
    QRadioButton::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT};
                                       {dot_rule} }}

    QTabWidget::pane {{ border: none; background: {BG}; }}
    QTabBar {{ background: transparent; qproperty-drawBase: 0; }}
    QTabBar::tab {{
        background: transparent; color: {MUTED}; padding: 9px 18px;
        border: none; border-bottom: 2px solid transparent; font-size: 13px;
        font-weight: 500; margin-right: 2px;
    }}
    QTabBar::tab:hover {{ color: {TEXT}; }}
    QTabBar::tab:selected {{ color: {TEXT}; border-bottom-color: {ACCENT};
                             font-weight: 600; }}

    QListWidget, QTableWidget, QTreeWidget {{
        background: {PANEL}; border: 1px solid {BORDER_SOFT}; border-radius: 9px;
        outline: none; padding: 4px;
    }}
    QListWidget::item {{ padding: 8px 10px; border-radius: 6px; margin: 1px 2px; }}
    QListWidget::item:hover {{ background: {ELEVATED}; }}
    QListWidget::item:selected {{ background: {ACCENT_DIM}; color: #ffffff; }}

    QTableWidget {{ gridline-color: transparent; }}
    QTableWidget::item {{ padding: 8px 6px; border: none; }}
    QTableWidget::item:selected {{ background: {ELEVATED}; color: {TEXT}; }}
    QHeaderView::section {{
        background: transparent; color: {FAINT}; border: none;
        border-bottom: 1px solid {BORDER_SOFT}; padding: 7px 6px;
        font-size: 11px; font-weight: 700; text-transform: uppercase;
    }}
    QTableCornerButton::section {{ background: transparent; border: none; }}

    QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: #333b4a; border-radius: 5px;
                                   min-height: 28px; }}
    QScrollBar::handle:vertical:hover {{ background: #404a5c; }}
    QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: #333b4a; border-radius: 5px;
                                     min-width: 28px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QSlider::groove:horizontal {{ height: 5px; background: #2b3140; border-radius: 3px; }}
    QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
    QSlider::handle:horizontal {{
        background: #ffffff; width: 14px; height: 14px; margin: -5px 0;
        border-radius: 7px;
    }}
    QSlider::handle:horizontal:hover {{ background: #dce4f5; }}

    QSplitter::handle {{ background: {BORDER_SOFT}; }}
    QSplitter::handle:horizontal {{ width: 1px; }}
    QSplitter::handle:vertical {{ height: 1px; }}

    QMenuBar {{ background: {PANEL}; border-bottom: 1px solid {BORDER_SOFT}; }}
    QMenuBar::item {{ padding: 6px 11px; background: transparent; }}
    QMenuBar::item:selected {{ background: {ELEVATED}; border-radius: 5px; }}
    QMenu {{ background: {ELEVATED}; border: 1px solid {BORDER};
             border-radius: 8px; padding: 5px; }}
    QMenu::item {{ padding: 7px 24px 7px 14px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {ACCENT_DIM}; }}
    QMenu::separator {{ height: 1px; background: {BORDER_SOFT}; margin: 5px 8px; }}

    QStatusBar {{ background: {PANEL}; border-top: 1px solid {BORDER_SOFT};
                  color: {MUTED}; }}
    QStatusBar::item {{ border: none; }}

    QProgressBar {{ background: #11131a; border: 1px solid {BORDER};
                    border-radius: 6px; height: 9px; text-align: center; }}
    QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}

    QToolTip {{ background: {ELEVATED}; color: {TEXT}; border: 1px solid {BORDER};
                padding: 6px 8px; border-radius: 6px; }}
    """
