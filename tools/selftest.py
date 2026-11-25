#!/usr/bin/env python3
"""Check that this machine can run SentryView, and say what is wrong if not.

    python3 tools/selftest.py
    python3 tools/selftest.py --url rtsp://192.168.1.50:554/stream1
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"
results: list = []


def check(name: str, status: str, detail: str = "") -> None:
    results.append((name, status, detail))
    mark = {PASS: "  ok  ", FAIL: " FAIL ", WARN: " warn "}[status]
    print(f"[{mark}] {name}" + (f"\n          {detail}" if detail else ""))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", help="also test a real camera URL")
    parser.add_argument("--transport", default="tcp", choices=["tcp", "udp"])
    args = parser.parse_args()

    print("SentryView self-test\n" + "-" * 52)

    version = sys.version_info
    check("Python 3.9 or newer",
          PASS if version >= (3, 9) else FAIL,
          f"found {version.major}.{version.minor}.{version.micro}")

    try:
        from PySide6.QtCore import qVersion
        from PySide6.QtWidgets import QApplication
        QApplication([])
        check("Qt / PySide6", PASS, f"Qt {qVersion()}")
    except Exception as exc:
        check("Qt / PySide6", FAIL, f"{exc}\n          pip install PySide6")
        return report()

    from sentryview.paths import app_data_dir, find_ffmpeg

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        check("FFmpeg", FAIL, "not found — pip install imageio-ffmpeg")
        return report()
    try:
        banner = subprocess.run([ffmpeg, "-hide_banner", "-version"],
                                capture_output=True, timeout=20)
        first = banner.stdout.decode("utf-8", "replace").splitlines()[0]
        check("FFmpeg", PASS, f"{first}\n          {ffmpeg}")
    except Exception as exc:
        check("FFmpeg", FAIL, str(exc))
        return report()

    muxers = subprocess.run([ffmpeg, "-hide_banner", "-muxers"],
                            capture_output=True, timeout=20)
    demuxers = subprocess.run([ffmpeg, "-hide_banner", "-demuxers"],
                              capture_output=True, timeout=20)
    has_segment = b" segment" in muxers.stdout
    has_rtsp = b" rtsp" in demuxers.stdout
    check("FFmpeg can read RTSP", PASS if has_rtsp else FAIL)
    check("FFmpeg can write day-split clips", PASS if has_segment else FAIL)

    data = app_data_dir()
    try:
        probe = data / ".writetest"
        probe.write_text("ok")
        probe.unlink()
        check("Settings folder is writable", PASS, str(data))
    except OSError as exc:
        check("Settings folder is writable", FAIL, f"{data}: {exc}")

    from sentryview.config import Config
    config = Config.load()
    check("Configuration loads", PASS,
          f"{len(config.cameras)} camera(s) configured")

    storage = config.storage
    if storage.enabled and storage.root:
        root = Path(storage.root).expanduser()
        try:
            root.mkdir(parents=True, exist_ok=True)
            probe = root / ".writetest"
            probe.write_text("ok")
            probe.unlink()
            from sentryview.retention import RetentionManager
            from sentryview.util import human_size
            free = RetentionManager(root).disk_free_bytes()
            check("Recording folder is writable", PASS,
                  f"{root}\n          {human_size(free)} free")
        except OSError as exc:
            check("Recording folder is writable", FAIL, f"{root}: {exc}")
    else:
        check("Recording", WARN, "turned off — live view only")

    targets = []
    if args.url:
        targets.append(("--url", args.url, args.transport))
    targets += [(c.name, c.effective_url(), c.transport) for c in config.cameras]

    if not targets:
        check("Camera reachable", WARN,
              "no cameras configured; pass --url to test one")
    else:
        from sentryview.probe import grab_frame
        for name, url, transport in targets:
            result = grab_frame(url, transport, timeout=25)
            check(f"Camera: {name}", PASS if result.ok else FAIL,
                  result.summary())

    return report()


def report() -> int:
    failures = sum(1 for _, status, _ in results if status == FAIL)
    print("-" * 52)
    if failures:
        print(f"{failures} check(s) failed.")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
