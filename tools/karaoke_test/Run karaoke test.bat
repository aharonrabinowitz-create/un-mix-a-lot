@echo off
rem Drag a song onto this file. Everything stays inside this folder (.venv, out).
setlocal
cd /d "%~dp0"
if "%~1"=="" (echo Drag a song onto this file. & pause & exit /b 1)
where uv >nul 2>&1 || (echo uv not found. Install it from https://docs.astral.sh/uv/ & pause & exit /b 1)
if not exist ".venv\Scripts\python.exe" (
  uv venv --python 3.12 .venv || (pause & exit /b 1)
  uv pip install --python .venv\Scripts\python.exe torch==2.11.0 --index-url https://download.pytorch.org/whl/cu128 || (pause & exit /b 1)
  uv pip install --python .venv\Scripts\python.exe "audio-separator" || (pause & exit /b 1)
)
.venv\Scripts\python.exe karaoke_test.py "%~1"
echo.
pause
