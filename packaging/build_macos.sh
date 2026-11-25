#!/usr/bin/env bash
# Build SentryView.app and wrap it in a drag-to-install DMG.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

APP_NAME="SentryView"
VERSION="$(python3 -c "import re,pathlib;print(re.search(r'__version__ = \"([^\"]+)\"',pathlib.Path('sentryview/__init__.py').read_text()).group(1))")"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
DIST="$ROOT/dist"
ARCH="$(uname -m)"
DMG="$DIST/${APP_NAME}-${VERSION}-macOS-${ARCH}.dmg"

echo "==> Building $APP_NAME $VERSION for macOS ($ARCH)"

"$PY" -c "import PyInstaller" 2>/dev/null || { echo "PyInstaller missing: $PY -m pip install pyinstaller"; exit 1; }

echo "==> Generating icons"
"$PY" packaging/make_icon.py
if [ ! -f assets/icon.icns ]; then
  WORK="$(mktemp -d)/icon.iconset"; mkdir -p "$WORK"
  cp assets/icon_16.png   "$WORK/icon_16x16.png"
  cp assets/icon_32.png   "$WORK/icon_16x16@2x.png"
  cp assets/icon_32.png   "$WORK/icon_32x32.png"
  cp assets/icon_64.png   "$WORK/icon_32x32@2x.png"
  cp assets/icon_128.png  "$WORK/icon_128x128.png"
  cp assets/icon_256.png  "$WORK/icon_128x128@2x.png"
  cp assets/icon_256.png  "$WORK/icon_256x256.png"
  cp assets/icon_512.png  "$WORK/icon_256x256@2x.png"
  cp assets/icon_512.png  "$WORK/icon_512x512.png"
  cp assets/icon_1024.png "$WORK/icon_512x512@2x.png"
  iconutil -c icns "$WORK" -o assets/icon.icns
fi

echo "==> Running PyInstaller"
rm -rf build "$DIST/$APP_NAME" "$DIST/$APP_NAME.app"
SENTRYVIEW_ROOT="$ROOT" "$PY" -m PyInstaller --noconfirm --clean \
  --distpath "$DIST" --workpath "$ROOT/build" packaging/SentryView.spec

APP="$DIST/$APP_NAME.app"
[ -d "$APP" ] || { echo "build failed: no $APP"; exit 1; }

echo "==> Fixing up the bundled ffmpeg"
FF="$APP/Contents/Frameworks/bin/ffmpeg"
[ -f "$FF" ] || FF="$APP/Contents/Resources/bin/ffmpeg"
[ -f "$FF" ] || { echo "ffmpeg missing from bundle"; exit 1; }
chmod +x "$FF"
"$FF" -hide_banner -version | head -1

echo "==> Ad-hoc signing"
# Not a Developer ID signature: it satisfies the loader on Apple Silicon and
# keeps the bundle self-consistent, but Gatekeeper still warns on first open.
codesign --force --deep --sign - "$APP" 2>/dev/null || echo "   (codesign skipped)"
codesign --verify --deep "$APP" 2>/dev/null && echo "   signature OK" || true

echo "==> Building DMG"
STAGE="$(mktemp -d)/dmg"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cat > "$STAGE/Read Me First.txt" <<'TXT'
SentryView
==========

1. Drag SentryView to the Applications folder on the right.
2. The first time you open it, macOS will say it is from an
   unidentified developer, because this build is not signed with a
   paid Apple Developer certificate.

   To open it anyway:
     - Right-click (or Control-click) SentryView in Applications
     - Choose "Open"
     - Click "Open" in the dialog

   You only have to do this once.

3. If macOS says the app "is damaged and can't be opened", that is the
   download quarantine flag. Remove it by running this in Terminal:

     xattr -dr com.apple.quarantine /Applications/SentryView.app

On macOS 15 (Sequoia) and later, allow SentryView to find devices on
your local network when prompted - cameras will not connect otherwise.
TXT

rm -f "$DMG"
hdiutil create -volname "$APP_NAME" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"

echo
echo "==> Done"
du -sh "$APP" | awk '{print "    app: " $1 "  " $2}'
du -sh "$DMG" | awk '{print "    dmg: " $1 "  " $2}'
