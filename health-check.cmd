@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment not found. Run setup.cmd first.
  exit /b 1
)
".venv\Scripts\python.exe" assistant.py --health-check
exit /b %errorlevel%
