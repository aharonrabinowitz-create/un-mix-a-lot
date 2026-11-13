@echo off
rem Double-click to install Un-Mix-a-Lot on Windows. It gets its own Python, so you don't need to install anything first.
cd /d "%~dp0"
echo Installing Un-Mix-a-Lot. This takes about 5 to 15 minutes and downloads a few GB. Leave this window open.
echo.
set "UV_UNMANAGED_INSTALL=%CD%\.tools"
if not exist ".tools\uv.exe" (
  echo Getting the Python helper (uv^) ...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  if errorlevel 1 goto :fail
)
if not exist ".venv\Scripts\python.exe" (
  echo Getting Python 3.12 ...
  ".tools\uv.exe" venv --seed --python 3.12 .venv
  if errorlevel 1 goto :fail
)
".venv\Scripts\python.exe" install.py %*
if errorlevel 1 goto :fail
echo.
echo All done. Double-click "Un-Mix-a-Lot.vbs" in this folder to start it.
if not defined UNMIX_NOPAUSE pause
exit /b 0
:fail
echo.
echo The install stopped with an error. Copy everything above this line and send it to whoever is helping you.
if not defined UNMIX_NOPAUSE pause
exit /b 1
