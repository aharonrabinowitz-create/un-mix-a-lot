@echo off
rem Drag a song onto this file. Everything stays inside this folder (.venv, out).
setlocal
cd /d "%~dp0"
if "%~1"=="" (echo Drag a song onto this file. & pause & exit /b 1)
set "UV=uv"
where uv >nul 2>&1
if errorlevel 1 set "UV=%LOCALAPPDATA%\Un-Mix-a-Lot\.tools\uv.exe"
if /i not "%UV%"=="uv" if not exist "%UV%" (echo uv not found. Install Un-Mix-a-Lot first, or get uv from https://docs.astral.sh/uv/ & pause & exit /b 1)
if not exist ".venv\Scripts\python.exe" (
  "%UV%" venv --python 3.12 .venv || (pause & exit /b 1)
  "%UV%" pip install --python .venv\Scripts\python.exe torch==2.11.0 --index-url https://download.pytorch.org/whl/cu128 || (pause & exit /b 1)
  "%UV%" pip install --python .venv\Scripts\python.exe "audio-separator" || (pause & exit /b 1)
)
.venv\Scripts\python.exe -c "import onnxruntime" >nul 2>&1
if errorlevel 1 "%UV%" pip install --python .venv\Scripts\python.exe onnxruntime
.venv\Scripts\python.exe karaoke_chain.py "%~1"
echo.
pause
