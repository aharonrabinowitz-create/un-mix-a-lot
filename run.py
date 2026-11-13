#!/usr/bin/env python3
"""Launch the app with the project's virtual environment."""
import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).parent
py = root / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
if not py.exists():
    sys.exit("Run `python install.py` first.")
os.environ.setdefault("STEMSPLIT_OPEN", "1")  # open the browser automatically
sys.exit(subprocess.call([str(py), str(root / "app.py")], cwd=root, env=os.environ))
