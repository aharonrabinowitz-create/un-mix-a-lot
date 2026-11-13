@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Un-Mix-a-Lot is not installed yet. Run:  python install.py
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" desktop.py
