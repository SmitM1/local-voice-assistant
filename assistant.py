from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import keyboard
import numpy as np
import pyperclip
import requests
import sounddevice as sd
import soundfile as sf
from pydantic import BaseModel, ConfigDict, Field, ValidationError


PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_DIR / "config.json"
SAMPLE_RATE = 16_000
CHANNELS = 1


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpenAppArgs(StrictModel):
    app: str


class SearchFilesArgs(StrictModel):
    query: str = Field(min_length=1, max_length=120)
    location: str


class DictateTextArgs(StrictModel):
    text: str = Field(min_length=1, max_length=10_000)


@dataclass(frozen=True)
class AppConfig:
    whisper_exe: Path
    whisper_model: Path
    ollama_url: str
    ollama_model: str
    piper_voice: str
    piper_data_dir: Path
    apps: dict[str, list[str]]
    search_locations: dict[str, Path]
    push_to_talk_key: str
    quit_key: str
    tts_enabled: bool
    max_search_results: int


def load_config() -> AppConfig:
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return AppConfig(
        whisper_exe=Path(raw["whisper_exe"]),
        whisper_model=Path(raw["whisper_model"]),
        ollama_url=raw["ollama_url"],
        ollama_model=raw["ollama_model"],
        piper_voice=raw["piper_voice"],
        piper_data_dir=Path(raw["piper_data_dir"]),
        apps={name.casefold(): command for name, command in raw["apps"].items()},
        search_locations={
            name.casefold(): Path(path) for name, path in raw["search_locations"].items()
        },
        push_to_talk_key=raw.get("push_to_talk_key", "f8"),
        quit_key=raw.get("quit_key", "esc"),
        tts_enabled=bool(raw.get("tts_enabled", True)),
        max_search_results=int(raw.get("max_search_results", 15)),
    )


class Recorder:
    def __init__(self) -> None:
        self._chunks: list[np.ndarray[Any, Any]] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()

    def _callback(
        self,
        indata: np.ndarray[Any, Any],
        _frames: int,
        _time_info: Any,
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            print(f"[microphone] {status}", file=sys.stderr)
        with self._lock:
            self._chunks.append(indata.copy())

    def start(self) -> None:
        if self._stream is not None:
            return
        with self._lock:
            self._chunks.clear()
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

    def stop(self, output_path: Path) -> float:
        if self._stream is None:
            return 0.0
        self._stream.stop()
        self._stream.close()
        self._stream = None

        with self._lock:
            chunks = list(self._chunks)
            self._chunks.clear()

        if not chunks:
            return 0.0
        audio = np.concatenate(chunks, axis=0)
        sf.write(output_path, audio, SAMPLE_RATE, subtype="PCM_16")
        return len(audio) / SAMPLE_RATE

    def cancel(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            self._chunks.clear()


class VoiceAssistant:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.recorder = Recorder()
        self.history: list[dict[str, Any]] = []
        self._validate_installation()

    def _validate_installation(self) -> None:
        problems: list[str] = []
        if not self.config.whisper_exe.is_file():
            problems.append(f"whisper executable not found: {self.config.whisper_exe}")
        if not self.config.whisper_model.is_file():
            problems.append(f"whisper model not found: {self.config.whisper_model}")
        if not self.config.piper_data_dir.is_dir():
            problems.append(f"Piper voice directory not found: {self.config.piper_data_dir}")
        else:
            voice_model = self.config.piper_data_dir / f"{self.config.piper_voice}.onnx"
            voice_config = self.config.piper_data_dir / f"{self.config.piper_voice}.onnx.json"
            if not voice_model.is_file() or not voice_config.is_file():
                problems.append(
                    f"Piper voice '{self.config.piper_voice}' is incomplete in "
                    f"{self.config.piper_data_dir}"
                )
        for name, root in self.config.search_locations.items():
            if not root.is_dir():
                problems.append(f"search location '{name}' not found: {root}")
        if problems:
            raise RuntimeError("\n".join(problems))

    def transcribe(self, audio_path: Path) -> str:
        output_base = audio_path.with_suffix("")
        command = [
            str(self.config.whisper_exe),
            "-m",
            str(self.config.whisper_model),
            "-f",
            str(audio_path),
            "-l",
            "auto",
            "-nt",
            "-otxt",
            "-of",
            str(output_base),
        ]
        result = subprocess.run(
            command,
            cwd=self.config.whisper_exe.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=120,
        )
        transcript_path = output_base.with_suffix(".txt")
        if result.returncode != 0 or not transcript_path.is_file():
            details = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"Whisper transcription failed: {details}")
        transcript = transcript_path.read_text(encoding="utf-8").strip()
        transcript_path.unlink(missing_ok=True)
        return transcript

    def ask_ollama(self, transcript: str) -> tuple[str, list[dict[str, Any]]]:
        system_prompt = (
            "You are a concise, fully local conversational assistant. You have no tools "
            "and cannot perform computer actions. Never claim to open apps, search files, "
            "type text, run commands, execute scripts, download, delete, or change the "
            "system. Answer ordinary questions briefly in plain text."
        )
        messages = [{"role": "system", "content": system_prompt}, *self.history]
        messages.append({"role": "user", "content": transcript})
        payload = {
            "model": self.config.ollama_model,
            "messages": messages,
            "stream": False,
            "think": False,
            # Unload after each response so Ollama and Whisper do not compete for
            # the laptop's 6 GB of VRAM on the next push-to-talk cycle.
            "keep_alive": 0,
            "options": {"temperature": 0, "num_ctx": 4096},
        }
        response = requests.post(self.config.ollama_url, json=payload, timeout=180)
        response.raise_for_status()
        data = response.json()
        message = data.get("message", {})
        content = str(message.get("content", "")).strip()

        self.history.extend(
            [
                {"role": "user", "content": transcript},
                {"role": "assistant", "content": content},
            ]
        )
        self.history = self.history[-10:]
        return content, []

    def deterministic_tool_call(self, transcript: str) -> dict[str, Any] | None:
        """Handle explicit core commands without asking an LLM to rewrite arguments."""
        cleaned = transcript.strip()
        lowered = cleaned.casefold()

        for prefix in ("dictate:", "dictate ", "type:", "type "):
            if lowered.startswith(prefix):
                text = cleaned[len(prefix) :].strip()
                if text:
                    return {
                        "function": {
                            "name": "dictate_text",
                            "arguments": {"text": text},
                        }
                    }

        app_aliases = {
            "notepad": "notepad",
            "calculator": "calculator",
            "calc": "calculator",
            "explorer": "explorer",
            "file explorer": "explorer",
            "visual studio code": "visual studio code",
            "vs code": "visual studio code",
            "vscode": "visual studio code",
        }
        open_match = re.fullmatch(
            r"(?:(?:please|can you|could you)\s+)?(?:open|launch|start)\s+(.+?)[.!]?",
            lowered,
        )
        if open_match:
            app = app_aliases.get(open_match.group(1).strip())
            if app in self.config.apps:
                return {"function": {"name": "open_app", "arguments": {"app": app}}}

        locations = "|".join(re.escape(name) for name in self.config.search_locations)
        search_match = re.fullmatch(
            rf"(?:please\s+)?search\s+(?:for\s+)?(.+?)\s+in\s+({locations})[.!]?",
            lowered,
        )
        if search_match:
            return {
                "function": {
                    "name": "search_files",
                    "arguments": {
                        "query": search_match.group(1).strip(),
                        "location": search_match.group(2).strip(),
                    },
                }
            }
        return None

    def execute_tool(self, tool_call: dict[str, Any]) -> str:
        function = tool_call.get("function") or {}
        name = function.get("name")
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be an object")

        if name == "open_app":
            args = OpenAppArgs.model_validate(arguments)
            app_name = args.app.casefold()
            command = self.config.apps.get(app_name)
            if command is None:
                raise ValueError(f"'{args.app}' is not an approved application")
            subprocess.Popen(command, shell=False)
            return f"Opened {app_name}."

        if name == "search_files":
            args = SearchFilesArgs.model_validate(arguments)
            location = args.location.casefold()
            root = self.config.search_locations.get(location)
            if root is None:
                raise ValueError(f"'{args.location}' is not an approved search location")
            matches = self._search_files(root, args.query)
            if not matches:
                return f"I found no files matching {args.query} in {location}."
            print("\nSearch results:")
            for match in matches:
                print(f"  {match}")
            suffix = "result" if len(matches) == 1 else "results"
            return f"I found {len(matches)} {suffix}. They are listed in the assistant window."

        if name == "dictate_text":
            args = DictateTextArgs.model_validate(arguments)
            pyperclip.copy(args.text)
            time.sleep(0.15)
            keyboard.press_and_release("ctrl+v")
            return "Dictation pasted."

        raise ValueError(f"Unapproved tool: {name}")

    def _search_files(self, root: Path, query: str) -> list[Path]:
        query_folded = query.casefold().strip()
        matches: list[Path] = []
        skipped_directories = {
            ".git",
            ".venv",
            "node_modules",
            "__pycache__",
            "build",
        }
        for current_root, directories, files in os.walk(root, topdown=True):
            directories[:] = [
                name
                for name in directories
                if name.casefold() not in skipped_directories and not name.startswith(".")
            ]
            for file_name in files:
                if query_folded in file_name.casefold():
                    matches.append(Path(current_root) / file_name)
                    if len(matches) >= self.config.max_search_results:
                        return matches
        return matches

    def speak(self, text: str) -> None:
        if not self.config.tts_enabled or not text.strip():
            return
        command = [
            sys.executable,
            "-m",
            "piper",
            "-m",
            self.config.piper_voice,
            "--data-dir",
            str(self.config.piper_data_dir),
            "--",
            text,
        ]
        subprocess.run(
            command,
            cwd=PROJECT_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            timeout=120,
            check=False,
        )

    def process_audio(self, audio_path: Path) -> None:
        transcript = self.transcribe(audio_path)
        if not transcript:
            print("No speech detected.")
            return
        print(f"You: {transcript}")
        direct_call = self.deterministic_tool_call(transcript)
        if direct_call is not None:
            content, tool_calls = "", [direct_call]
        else:
            content, tool_calls = self.ask_ollama(transcript)

        if len(tool_calls) > 1:
            reply = "I refused multiple simultaneous actions. Please ask for one action at a time."
        elif tool_calls:
            try:
                reply = self.execute_tool(tool_calls[0])
            except (ValidationError, ValueError, OSError, json.JSONDecodeError) as exc:
                reply = f"I could not perform that approved action: {exc}"
        else:
            reply = content or "I did not receive a usable response."

        print(f"Assistant: {reply}")
        self.speak(reply)

    def run(self) -> None:
        print("Local Voice Assistant")
        print(f"Hold {self.config.push_to_talk_key.upper()} to talk; press {self.config.quit_key.upper()} to quit.")
        print("Script execution and arbitrary shell commands are disabled.\n")

        event_queue: queue.Queue[keyboard.KeyboardEvent] = queue.Queue()
        keyboard.hook(event_queue.put)
        recording = False

        try:
            while True:
                event = event_queue.get()
                key_name = (event.name or "").casefold()
                if key_name == self.config.quit_key.casefold() and event.event_type == keyboard.KEY_DOWN:
                    if recording:
                        self.recorder.cancel()
                    break

                if key_name != self.config.push_to_talk_key.casefold():
                    continue

                if event.event_type == keyboard.KEY_DOWN and not recording:
                    self.recorder.start()
                    recording = True
                    print("Listening...", flush=True)
                elif event.event_type == keyboard.KEY_UP and recording:
                    recording = False
                    with tempfile.TemporaryDirectory(prefix="local-voice-assistant-") as temp_dir:
                        audio_path = Path(temp_dir) / "utterance.wav"
                        duration = self.recorder.stop(audio_path)
                        if duration < 0.25:
                            print("Recording was too short.\n")
                            continue
                        print(f"Processing {duration:.1f}s of audio...")
                        try:
                            self.process_audio(audio_path)
                        except requests.RequestException as exc:
                            print(f"Ollama is unavailable: {exc}")
                        except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
                            print(f"Assistant error: {exc}")
                        print()
        finally:
            keyboard.unhook_all()
            self.recorder.cancel()
            print("Assistant stopped.")


def main() -> int:
    try:
        assistant = VoiceAssistant(load_config())
        assistant.run()
    except (OSError, KeyError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Startup error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
