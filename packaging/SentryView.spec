# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec shared by the macOS and Windows builds."""
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("SENTRYVIEW_ROOT", os.getcwd())).resolve()
sys.path.insert(0, str(ROOT))

APP_NAME = "SentryView"
IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"


def ffmpeg_source() -> str:
    """Locate the ffmpeg binary to bundle."""
    override = os.environ.get("SENTRYVIEW_FFMPEG")
    if override and Path(override).exists():
        return override
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


FFMPEG_TARGET = "ffmpeg.exe" if IS_WINDOWS else "ffmpeg"


def staged_ffmpeg() -> str:
    """Copy ffmpeg under the exact name the app looks for at runtime."""
    import shutil
    import tempfile

    source = ffmpeg_source()
    stage = Path(tempfile.gettempdir()) / "sentryview-build" / "bin"
    stage.mkdir(parents=True, exist_ok=True)
    target = stage / FFMPEG_TARGET
    shutil.copy2(source, target)
    target.chmod(0o755)
    return str(target)


FFMPEG = staged_ffmpeg()
print(f"[spec] bundling ffmpeg as bin/{FFMPEG_TARGET} from {FFMPEG}")

datas = [
    (str(ROOT / "assets"), "assets"),
]
binaries = [
    (FFMPEG, "bin"),
]

# Only QtCore / QtGui / QtWidgets are used; everything else is dead weight.
excludes = [
    "tkinter", "unittest", "pydoc_data", "lib2to3", "test",
    "numpy", "PIL", "matplotlib", "scipy", "pandas",
    "pytest", "setuptools", "pip", "wheel", "imageio_ffmpeg",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DRender",
    "PySide6.QtBluetooth", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtLocation",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtNfc",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtPositioning", "PySide6.QtQml",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtSensors", "PySide6.QtSerialBus", "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio", "PySide6.QtSql", "PySide6.QtStateMachine",
    "PySide6.QtSvgWidgets", "PySide6.QtTest", "PySide6.QtTextToSpeech",
    "PySide6.QtUiTools", "PySide6.QtWebChannel", "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets", "PySide6.QtXml", "PySide6.QtHttpServer",
    "PySide6.QtGraphs", "PySide6.QtGraphsWidgets", "PySide6.QtNetworkAuth",
]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"],
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / ("icon.ico" if IS_WINDOWS else "icon.icns"))
         if (ROOT / "assets" / ("icon.ico" if IS_WINDOWS else "icon.icns")).exists()
         else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)

if IS_MAC:
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=str(ROOT / "assets" / "icon.icns"),
        bundle_identifier="com.sentryview.app",
        version="1.1.1",
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": "1.1.1",
            "CFBundleVersion": "1.1.1",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
            "LSApplicationCategoryType": "public.app-category.utilities",
            # macOS 15+ gates LAN access; cameras live on the local network.
            "NSLocalNetworkUsageDescription":
                "SentryView connects to the cameras on your local network to "
                "show and record their video.",
            "NSBonjourServices": ["_rtsp._tcp"],
        },
    )
