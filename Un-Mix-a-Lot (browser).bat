@echo off
title Un-Mix-a-Lot
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Un-Mix-a-Lot is not installed yet. Run:  python install.py
  pause
  exit /b 1
)
set STEMSPLIT_OPEN=1
echo.
echo  Un-Mix-a-Lot (browser version) is starting. Your browser will open in a few seconds.
echo  Keep this window open while you use the app. Close it to quit.
echo.
".venv\Scripts\python.exe" app.py
echo.
echo  Un-Mix-a-Lot stopped.
pause
