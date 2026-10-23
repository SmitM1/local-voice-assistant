# Local Voice Assistant

A private, fully local Windows voice assistant using:

- `whisper.cpp` for speech recognition
- `llama3.2:3b` through Ollama for short conversational answers
- Piper for local speech output
- a fixed allowlist for desktop actions

Desktop actions are parsed deterministically and validated against fixed allowlists;
Ollama is never allowed to request or execute an action. The assistant never passes
model output to `cmd.exe` or PowerShell. Script execution, downloads, deletion, and
arbitrary shell commands are intentionally unavailable.

## Setup

Create your local configuration, update its paths for your computer, then run setup:

```cmd
copy config.example.json config.json
```

```cmd
setup.cmd
```

Make sure Ollama is running, then start the assistant:

```cmd
start.cmd
```

Hold **F8** while speaking and release it to process the request. Press **Esc** to
quit. Because the hotkey is global, you can focus another application before using
dictation.

Example requests:

- "Open Notepad"
- "Open Visual Studio Code"
- "Search for invoice in Downloads"
- "Dictate: The meeting has moved to three PM."
- "Explain recursion in one sentence"

## Configuration

Edit `config.json` to change the hotkey, model, TTS setting, approved applications,
or searchable folders. Application commands and search roots are selected by exact
allowlist keys; spoken text cannot supply executable paths.

Set `tts_enabled` to `false` to disable spoken responses.
