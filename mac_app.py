#!/usr/bin/env python3
"""Create the double-clickable Mac app 'Un-Mix-a-Lot.app' next to this file.

install.py runs this for you. Run it again (`.venv/bin/python mac_app.py`) if you move the project folder.
The app is only a thin launcher: it starts desktop.py with this folder's .venv, so updating the project
never means rebuilding the app. Nothing is downloaded, so macOS doesn't show an "unidentified developer" warning.
"""
import stat
import sys
from pathlib import Path

NAME = "Un-Mix-a-Lot"
BUNDLE_ID = "com.unmixalot.app"

LAUNCHER = r"""#!/bin/bash
APP="$(cd "$(dirname "$0")/../.." && pwd)"
ROOT="$(cat "$APP/Contents/Resources/root" 2>/dev/null)"
[ -x "$ROOT/.venv/bin/python" ] || ROOT="$(dirname "$APP")"
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  osascript -e 'display dialog "Un-Mix-a-Lot is not installed yet. Double-click \"Install Un-Mix-a-Lot\" in the project folder first." with title "Un-Mix-a-Lot" buttons {"OK"} default button "OK" with icon stop'
  exit 1
fi
cd "$ROOT"
exec "$ROOT/.venv/bin/python" "$ROOT/desktop.py"
"""

PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>{name}</string>
<key>CFBundleDisplayName</key><string>{name}</string>
<key>CFBundleIdentifier</key><string>{bid}</string>
<key>CFBundleExecutable</key><string>launcher</string>
<key>CFBundleIconFile</key><string>icon</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>1.0</string>
<key>CFBundleShortVersionString</key><string>1.0</string>
<key>LSMinimumSystemVersion</key><string>10.13</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
"""


def make(root: Path, dest: Path = None) -> Path:
    root = Path(root).resolve()
    app = Path(dest) if dest else root / f"{NAME}.app"
    macos, res = app / "Contents" / "MacOS", app / "Contents" / "Resources"
    macos.mkdir(parents=True, exist_ok=True)
    res.mkdir(parents=True, exist_ok=True)
    (app / "Contents" / "Info.plist").write_text(PLIST.format(name=NAME, bid=BUNDLE_ID), encoding="utf-8")
    (res / "root").write_text(str(root), encoding="utf-8")
    launcher = macos / "launcher"
    launcher.write_text(LAUNCHER, encoding="utf-8")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    ico = root / "icon.ico"
    if ico.exists():
        try:
            from PIL import Image
            Image.open(ico).convert("RGBA").resize((256, 256)).save(res / "icon.icns", format="ICNS")
        except Exception as e:  # the app still works, it just gets a generic icon
            print("Couldn't make the app icon:", e)
    return app


if __name__ == "__main__":
    out = make(Path(__file__).parent)
    print(f"Created {out}")
    if sys.platform != "darwin":
        print("(This is only useful on a Mac.)")
