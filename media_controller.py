from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import keyboard
import psutil
import win32gui
import win32process


MediaAction = Literal[
    "toggle", "stop", "next", "previous", "fullscreen", "volume_down", "volume_up", "mute"
]


@dataclass(frozen=True)
class MediaInstruction:
    action: MediaAction
    amount: int = 1
    position: int = 0


def _normalized_request(transcript: str) -> str:
    text = transcript.strip().casefold().rstrip(".!?")
    return re.sub(
        r"^(?:please\s+)?(?:i\s+want\s+you\s+to|can\s+you|could\s+you|would\s+you)\s+",
        "",
        text,
    )


def parse_media_requests(transcript: str) -> tuple[MediaInstruction, ...]:
    text = _normalized_request(transcript)
    instructions: list[MediaInstruction] = []
    media_context = bool(
        re.search(
            r"\b(?:video|media|playback|track|song|chrome|browser|volume|fullscreen|full screen)\b",
            text,
        )
    )
    current_media_context = bool(
        re.search(
            r"\b(?:current|the)\s+(?:video|media|playback|track|song)\b|"
            r"\b(?:chrome|browser|media|playback|fullscreen|full screen|volume)\b",
            text,
        )
    )

    toggle = re.search(r"\b(?:play|pause|resume|play[ /-]?pause)\b", text)
    if toggle and (current_media_context or toggle.group(0) == text):
        instructions.append(MediaInstruction("toggle", position=toggle.start()))
    stop = re.search(r"\bstop\b(?=[^.!?]*(?:video|media|playback))", text)
    if stop:
        instructions.append(MediaInstruction("stop", position=stop.start()))
    next_item = re.search(r"\b(?:next|skip)(?:\s+(?:track|song|video))?\b", text)
    if next_item:
        instructions.append(MediaInstruction("next", position=next_item.start()))
    previous = re.search(r"\b(?:previous|last)(?:\s+(?:track|song|video))?\b", text)
    if previous:
        instructions.append(MediaInstruction("previous", position=previous.start()))
    fullscreen = re.search(r"\b(?:full[ -]?screen|maximi[sz]e)\b", text)
    if fullscreen and media_context:
        instructions.append(MediaInstruction("fullscreen", position=fullscreen.start()))
    volume_down = re.search(
        r"\b(?:lower|reduce|decrease|turn down)\b[^.!?]{0,25}\bvolume\b|"
        r"\bvolume\b[^.!?]{0,25}\b(?:down|lower)\b",
        text,
    )
    if volume_down:
        amount = 2 if re.search(r"\b(?:little|bit|slightly)\b", text) else 4
        instructions.append(MediaInstruction("volume_down", amount, volume_down.start()))
    volume_up = re.search(
        r"\b(?:raise|increase|turn up)\b[^.!?]{0,25}\bvolume\b|"
        r"\bvolume\b[^.!?]{0,25}\b(?:up|higher)\b",
        text,
    )
    if volume_up:
        amount = 2 if re.search(r"\b(?:little|bit|slightly)\b", text) else 4
        instructions.append(MediaInstruction("volume_up", amount, volume_up.start()))
    mute = re.search(r"\b(?:mute|unmute)\b", text)
    if mute:
        instructions.append(MediaInstruction("mute", position=mute.start()))
    return tuple(sorted(instructions, key=lambda item: item.position))


def parse_media_request(transcript: str) -> MediaAction | None:
    instructions = parse_media_requests(transcript)
    return instructions[0].action if len(instructions) == 1 else None


class MediaController:
    """Deterministic wrapper around Windows media keys and browser fullscreen."""

    @staticmethod
    def _foreground_process() -> str:
        hwnd = int(win32gui.GetForegroundWindow())
        if not hwnd:
            return ""
        _thread_id, process_id = win32process.GetWindowThreadProcessId(hwnd)
        return psutil.Process(process_id).name().casefold()

    def execute(self, action: MediaAction, amount: int = 1) -> str:
        if action == "fullscreen":
            if self._foreground_process() not in {
                "chrome.exe",
                "msedge.exe",
                "firefox.exe",
                "brave.exe",
                "opera.exe",
            }:
                raise ValueError("Bring the browser video to the foreground before fullscreening it")
            keyboard.press_and_release("f11")
            return "Made the foreground browser fullscreen."
        media_key = {
            "toggle": "play/pause media",
            "stop": "stop media",
            "next": "next track",
            "previous": "previous track",
            "volume_down": "volume down",
            "volume_up": "volume up",
            "mute": "volume mute",
        }[action]
        for _ in range(max(amount, 1)):
            keyboard.press_and_release(media_key)
        return {
            "toggle": "Sent Play/Pause to the current media.",
            "stop": "Stopped media playback.",
            "next": "Skipped to the next media item.",
            "previous": "Returned to the previous media item.",
            "volume_down": "Lowered the volume.",
            "volume_up": "Raised the volume.",
            "mute": "Toggled mute.",
        }[action]

    def execute_many(self, instructions: tuple[MediaInstruction, ...]) -> tuple[str, ...]:
        return tuple(self.execute(item.action, item.amount) for item in instructions)
