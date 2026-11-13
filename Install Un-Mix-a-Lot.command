#!/bin/bash
# Double-click to install Un-Mix-a-Lot on a Mac. It gets its own Python, so you don't need to install anything first.
# (First time macOS may ask: right-click this file, choose Open, then Open again.)
cd "$(dirname "$0")" || exit 1
pause() { echo; read -n 1 -s -r -p "Press any key to close this window."; echo; }
echo "Installing Un-Mix-a-Lot. This takes about 5 to 15 minutes and downloads a few GB. Leave this window open."
echo

export UV_UNMANAGED_INSTALL="$PWD/.tools"   # keep the helper tool inside this folder, change nothing else
UV="$PWD/.tools/uv"
if [ ! -x "$UV" ]; then
  echo "Getting the Python helper (uv) …"
  if ! curl -LsSf https://astral.sh/uv/install.sh | sh; then
    echo; echo "Couldn't download the helper. Check your internet connection and try again."; pause; exit 1
  fi
fi
if [ ! -x ".venv/bin/python" ]; then
  echo "Getting Python 3.12 …"
  "$UV" venv --seed --python 3.12 .venv || { echo; echo "Couldn't set up Python."; pause; exit 1; }
fi
if ! .venv/bin/python install.py "$@"; then
  echo; echo "The install stopped with an error. Copy everything above this line and send it to whoever is helping you."
  pause; exit 1
fi
echo
echo "All done. Double-click \"Un-Mix-a-Lot\" in this folder to start it."
open . 2>/dev/null
pause
