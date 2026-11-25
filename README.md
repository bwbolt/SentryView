# SentryView

A desktop app for watching and recording the RTSP cameras on your own network.
No account, no cloud, no subscription — video goes straight from your cameras
to your computer and nowhere else.

- **Unlimited cameras.** Add any camera that speaks RTSP, by URL.
- **Live wall.** Auto, 1×1, 2×2, 3×3 or 4×4 grid; double-click any tile to
  maximise it.
- **Continuous recording, split by day.** Optional. Clips land in dated
  folders so a given moment is easy to find.
- **Storage limits.** Optional age limit, size cap, or both. The oldest
  footage is removed automatically so the disk never fills.
- **Review interface.** Camera and day pickers, thumbnails, a clickable
  24-hour timeline, scrubbing, frame-stepping, 0.25×–8× speed, snapshots
  and clip export.

---

## Install

### macOS

1. Open `SentryView-1.0.0-macOS-x86_64.dmg`.
2. Drag **SentryView** to **Applications**.
3. **The first launch needs one extra step.** This build is not signed with a
   paid Apple Developer certificate, so macOS will refuse a normal
   double-click. Instead:
   - Right-click (or Control-click) SentryView in Applications
   - Choose **Open**
   - Click **Open** in the dialog

   You only do this once.

   If macOS instead says the app *"is damaged and can't be opened"*, that is
   the download quarantine flag. Clear it with:

   ```bash
   xattr -dr com.apple.quarantine /Applications/SentryView.app
   ```

4. On **macOS 15 (Sequoia) and later**, allow SentryView to find devices on
   your local network when prompted. Cameras will not connect otherwise.
   (System Settings → Privacy & Security → Local Network.)

> The provided DMG is an **Intel (x86_64)** build. It runs on Apple Silicon
> through Rosetta 2. For a native Apple Silicon build, see
> [Building from source](#building-from-source).

### Windows

1. Run `SentryView-1.0.0-Windows-Setup.exe`.
2. Windows SmartScreen will show *"Windows protected your PC"*, because the
   installer is not signed with a paid code-signing certificate. Click
   **More info → Run anyway**.
3. The installer is per-user and needs no administrator password.

A portable `.zip` is also provided if you would rather not install: unzip it
anywhere and run `SentryView.exe`.

---

## First run

A short setup asks two optional questions:

1. **Do you want to save video?** You can say no and use live view only;
   recording can be switched on later on the Storage page.
2. **Where, and for how long?** Pick a folder, then optionally an age limit
   (*delete clips older than N days*), a size cap (*keep total under N GB*),
   or neither.

Then add your first camera. You can skip that too and add cameras any time.

---

## Adding a camera

You need the camera's **RTSP URL**, which its own app or manual lists —
usually under Advanced, Network, or Developer settings. Some cameras need
RTSP (or ONVIF) switched on first.

Common shapes:

| Brand | URL |
| --- | --- |
| Hikvision | `rtsp://IP:554/Streaming/Channels/101` |
| Dahua / Amcrest | `rtsp://IP:554/cam/realmonitor?channel=1&subtype=0` |
| Reolink | `rtsp://IP:554/h264Preview_01_main` |
| UniFi Protect | `rtsp://IP:7447/<stream key>` |
| ONVIF generic | `rtsp://IP:554/onvif1` |

Put the username and password in the dedicated fields rather than in the URL
— they are escaped correctly for you, and the password is masked everywhere
it is displayed.

Press **Test connection** before saving. It pulls one real frame and shows
it, along with the resolution, codec and frame rate it found. If it fails you
get a specific reason (wrong password, no route, bad path) instead of a
generic error.

**Tip:** most cameras publish a lower-resolution *substream* (often
`subtype=1` or `_sub`). It is much lighter to view and record, and it is
usually plenty for an overview tile.

### No camera handy?

A fake RTSP camera ships with the source, so you can try the app before
touching real hardware:

```bash
python3 tools/test_camera.py --count 2
```

Then add `rtsp://127.0.0.1:8554/cam1`.

---

## How recording works

Each camera gets **one** connection to the camera, which feeds both the live
tile and the recording. Cheap cameras often cap concurrent clients, so this
matters.

Video is **stream-copied**, not re-encoded, so recording costs almost no CPU
and loses no quality. If a camera's codec cannot be stored in MP4, SentryView
notices and falls back to re-encoding on its own.

Clips are written as:

```
<your folder>/<camera>/2026-11-24/14-30-00.mp4
                      /2026-11-24/14-40-00.mp4
                      /2026-11-25/00-00-00.mp4
```

One folder per day, one file per clip, named by the time it starts. Clip
length is configurable (5–60 minutes, default 10) and clips break on clean
clock boundaries.

Each clip is a **fragmented MP4**, which means it stays playable even if the
app is force-quit or the machine loses power mid-write — you lose at most the
last couple of seconds, never the whole clip. Quitting normally (or logging
out) finalises the current clip properly.

### Storage limits

Both limits are optional and can be combined:

- **Age** — clips older than N days are deleted.
- **Size** — when the total exceeds N GB, the oldest clips go first.

Limits are applied at startup, whenever a clip rolls over, and on a timer —
every minute while a size cap is set, every five minutes otherwise.

The clip currently being written is never deleted, and clips keep growing
between sweeps, so a size cap is enforced *closely* rather than exactly:
SentryView prunes to 95% of the limit and real usage settles a little above
it, by roughly one sweep's worth of recording. With normal settings
(10-minute clips, a cap in the tens or hundreds of GB) that is a fraction of
a percent. Leave a little headroom rather than setting the cap to the last
free byte on the disk.

The Storage page shows what is used, what is free, and projects how many days
fit at the rate you are actually recording.

Deletion only ever touches files that match the exact recording layout, so
anything else you keep in that folder is left alone.

---

## Reviewing recordings

The **Recordings** tab: pick a camera, pick a day, pick a clip. The 24-hour
timeline under the player shows where footage exists — click anywhere on it
to jump straight to that moment.

| Key | Action |
| --- | --- |
| `Space` | Play / pause |
| `←` / `→` | Back / forward 5 seconds |
| `.` | Step one frame |
| `[` / `]` | Previous / next clip |

**Play continuously** rolls from one clip into the next, so a long event
plays without interruption. Right-click a clip to export a copy, open it in
your usual player, reveal it in Finder/Explorer, or delete it.

---

## Where things are stored

| | macOS | Windows |
| --- | --- | --- |
| Settings, thumbnails | `~/Library/Application Support/SentryView` | `%APPDATA%\SentryView` |
| Recordings | wherever you chose | wherever you chose |

Camera passwords are stored in `config.json`, in plain text, with
owner-only file permissions. Treat that file as a secret. (Setting
`SENTRYVIEW_DATA_DIR` moves the whole settings folder, which is handy for a
portable install on a USB stick.)

Uninstalling never deletes recordings.

---

## Troubleshooting

**The camera says "Reconnecting…"**
Hover the tile, or check the Cameras tab, for the specific error. Run **Test
connection** from the camera's settings for a fuller diagnosis.

**Works in VLC but not here**
Try UDP transport in the camera's settings. Some cameras are unhappy with
RTSP-over-TCP.

**Choppy live view, high CPU**
Point the camera at its substream, or lower the grid size. Live tiles are
downscaled before they reach the screen, but decoding several 4K streams is
still real work. Recording is unaffected either way — it never re-encodes.

**Clips play at the wrong speed or jump**
Turn on **Fix broken timestamps** in that camera's settings. Some cameras
produce unusable timestamps and need the wall clock substituted.

**Nothing is being recorded**
Check: recording enabled on the Storage page, a folder is set, and the camera
itself has *"Record this camera"* ticked (Cameras tab → Recording column).

---

## Building from source

Needs **Python 3.9+**. No Node, no Homebrew, no system FFmpeg — FFmpeg
arrives as a Python wheel and is bundled into the app.

```bash
git clone <this repo> && cd SentryView
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

.venv/bin/python main.py          # run it
```

### macOS (`.app` + `.dmg`)

```bash
.venv/bin/python -m pip install pyinstaller
./packaging/build_macos.sh
```

Output lands in `dist/`. The build targets the architecture of the machine it
runs on, so build on Apple Silicon to get a native Apple Silicon app.

### Windows (`.exe` installer + portable zip)

Must be run **on Windows** — a Windows executable cannot be produced from
macOS. Install [Inno Setup 6](https://jrsoftware.org/isdl.php) for the
installer (the portable zip is built without it).

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

### Both, without owning both machines

`.github/workflows/build.yml` builds the Windows installer, an Intel DMG and
an Apple Silicon DMG on GitHub's runners. Push the repo to GitHub, then:

- **Actions → Build installers → Run workflow**, and download the artifacts; or
- push a tag (`git tag v1.0.0 && git push --tags`) to get a release with all
  three attached.

Each build is smoke-tested in CI before it is uploaded.

---

## Licence

SentryView is MIT licensed — see `LICENSE.txt`.

It bundles **FFmpeg** (LGPL v2.1+, unmodified binary, source at ffmpeg.org)
and uses **Qt for Python / PySide6** (LGPL v3).
