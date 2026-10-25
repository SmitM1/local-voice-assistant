@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Python environment not found. Run setup.cmd first.
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" desktop_app.py
endlocal
