@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" py -3.13 -m venv .venv
".venv\Scripts\python.exe" -m pip install -r requirements.txt
where ollama >nul 2>nul
if not errorlevel 1 (
  ollama pull llama3.2:3b
  ollama pull qwen2.5-coder:3b
  ollama pull qwen3-vl:2b-instruct
  ollama create local-gui-vision -f Modelfile.qwen3-vl-4k
)
if not exist "voices" mkdir voices
if not exist "voices\en_US-lessac-high.onnx" ".venv\Scripts\python.exe" -m piper.download_voices en_US-lessac-high --data-dir voices
call install-desktop-shortcut.cmd
endlocal
