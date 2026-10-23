@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" py -3.13 -m venv .venv
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if not exist "voices" mkdir voices
if not exist "voices\en_US-lessac-medium.onnx" ".venv\Scripts\python.exe" -m piper.download_voices en_US-lessac-medium --data-dir voices
endlocal
