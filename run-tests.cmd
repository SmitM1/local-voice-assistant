@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment not found. Run setup.cmd first.
  exit /b 1
)
".venv\Scripts\python.exe" -m unittest discover -s tests -v
exit /b %errorlevel%
