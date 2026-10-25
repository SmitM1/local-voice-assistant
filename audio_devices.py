from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import sounddevice as sd


DeviceKind = Literal["input", "output"]


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    host_api: str
    max_input_channels: int
    max_output_channels: int
    default_samplerate: float

    @property
    def key(self) -> str:
        return f"{self.host_api}::{self.name}"

    @property
    def label(self) -> str:
        return f"{self.name}  ·  {self.host_api}"


def list_audio_devices(kind: DeviceKind) -> tuple[AudioDevice, ...]:
    devices: list[AudioDevice] = []
    host_apis = sd.query_hostapis()
    for index, raw in enumerate(sd.query_devices()):
        max_input = int(raw["max_input_channels"])
        max_output = int(raw["max_output_channels"])
        if kind == "input" and max_input < 1:
            continue
        if kind == "output" and max_output < 1:
            continue
        host_api = str(host_apis[int(raw["hostapi"])]["name"])
        devices.append(
            AudioDevice(
                index=index,
                name=str(raw["name"]),
                host_api=host_api,
                max_input_channels=max_input,
                max_output_channels=max_output,
                default_samplerate=float(raw["default_samplerate"]),
            )
        )
    # User-facing APIs first, while retaining specialist devices as fallbacks.
    priority = {"Windows WASAPI": 0, "Windows DirectSound": 1, "MME": 2}
    devices.sort(key=lambda item: (priority.get(item.host_api, 3), item.label.casefold()))
    return tuple(devices)


def resolve_audio_device(key: str, kind: DeviceKind) -> AudioDevice | None:
    normalized = key.strip()
    if not normalized:
        return None
    return next((item for item in list_audio_devices(kind) if item.key == normalized), None)


def save_audio_device_settings(
    config_path: Path, *, input_device: str, output_device: str
) -> None:
    """Atomically persist device keys while preserving all other configuration."""
    _save_config_values(
        config_path,
        input_audio_device=input_device,
        output_audio_device=output_device,
    )


def save_vad_threshold_setting(config_path: Path, threshold: float) -> None:
    """Persist the microphone capture threshold without restarting the app."""
    if not 0.0001 <= threshold <= 1.0:
        raise ValueError("Microphone threshold must be between 0.0001 and 1.0")
    _save_config_values(config_path, vad_rms_threshold=float(threshold))


def _save_config_values(config_path: Path, **changes: object) -> None:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw.update(changes)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{config_path.name}.", suffix=".tmp", dir=config_path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(raw, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, config_path)
    finally:
        temporary.unlink(missing_ok=True)
