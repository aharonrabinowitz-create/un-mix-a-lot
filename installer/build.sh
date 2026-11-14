#!/bin/bash
# Build "Un-Mix-a-Lot-Setup.exe" (Windows installer). Needs NSIS: sudo apt install nsis
# Usage: installer/build.sh [output-folder]
set -e
cd "$(dirname "$0")/.."
OUT="$(mkdir -p "${1:-dist}" && cd "${1:-dist}" && pwd)"
rm -rf installer/stage && mkdir -p installer/stage
cp app.py desktop.py install.py run.py run_mcp.py requirements.txt requirements-sam.txt icon.ico LICENSE README.md DISCLAIMER.txt splash.html installer/stage/
cp "Install Un-Mix-a-Lot.bat" "Un-Mix-a-Lot.bat" "Un-Mix-a-Lot (browser).bat" installer/stage/
mkdir installer/stage/stemsplit
(cd stemsplit && find . -name '*.py' -o -name 'LICENSE' | grep -v __pycache__ | while read f; do mkdir -p "../installer/stage/stemsplit/$(dirname "$f")"; cp "$f" "../installer/stage/stemsplit/$f"; done)
# Windows batch files must use CRLF line endings
for f in installer/stage/*.bat; do sed -i 's/\r$//; s/$/\r/' "$f"; done
cd installer
makensis -V2 -DOUTFILE="$OUT/Un-Mix-a-Lot-Setup.exe" UnMixALot.nsi
rm -rf stage
ls -la "$OUT/Un-Mix-a-Lot-Setup.exe"
