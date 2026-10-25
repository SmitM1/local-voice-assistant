from __future__ import annotations

import queue
import ctypes
import math
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

import pystray
from PIL import Image, ImageDraw, ImageTk

from assistant import CONFIG_PATH, VoiceAssistant, load_config
from audio_devices import (
    list_audio_devices,
    save_audio_device_settings,
    save_vad_threshold_setting,
)


_instance_mutex: Any = None

BG = "#090D12"
SURFACE = "#111821"
SURFACE_2 = "#18222D"
BORDER = "#253241"
TEXT = "#F1F5F9"
MUTED = "#91A0B2"
ACCENT = "#4CC9F0"
ACCENT_DARK = "#123644"
SUCCESS = "#39D98A"
DANGER = "#FF647C"
DANGER_DARK = "#3B1B25"
LOG_BG = "#080B10"


def claim_single_instance() -> bool:
    global _instance_mutex
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    _instance_mutex = kernel32.CreateMutexW(None, False, "Local\\LocalVoicePilot")
    return bool(_instance_mutex) and ctypes.get_last_error() != 183


class QueueWriter:
    def __init__(self, output: queue.Queue[str]) -> None:
        self.output = output

    def write(self, value: str) -> int:
        if value:
            self.output.put(value)
        return len(value)

    def flush(self) -> None:
        return None


def app_icon() -> Image.Image:
    image = Image.new("RGBA", (64, 64), (24, 28, 36, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((23, 8, 41, 39), radius=9, fill=(78, 196, 255, 255))
    draw.arc((15, 22, 49, 51), 0, 180, fill=(239, 244, 250, 255), width=5)
    draw.line((32, 49, 32, 57), fill=(239, 244, 250, 255), width=5)
    draw.line((22, 57, 42, 57), fill=(239, 244, 250, 255), width=5)
    return image


class VoiceAssistantDashboard:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Nova Voice Pilot")
        self.root.geometry("920x790")
        self.root.minsize(700, 640)
        self.root.configure(bg=BG)
        self._base_tk_scaling = float(self.root.tk.call("tk", "scaling"))
        self._ui_scale = 1.0
        self._scale_after_id: str | None = None
        self._configure_combobox_style()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.output: queue.Queue[str] = queue.Queue()
        self.assistant: VoiceAssistant | None = None
        self.worker: threading.Thread | None = None
        self.closing = False
        self.shutdown_requested = threading.Event()
        self.startup_error: str | None = None
        self.restart_pending = False
        self._device_assistant_id: int | None = None
        self.input_device_keys: dict[str, str] = {}
        self.output_device_keys: dict[str, str] = {}
        self._meter_min_db = -72.0
        self._meter_max_db = -6.0
        self._meter_threshold_rms = 0.003
        self._threshold_dragging = False
        self.tray: pystray.Icon | None = None
        self._set_dark_title_bar()
        self._window_icon = ImageTk.PhotoImage(app_icon().resize((32, 32)))
        self.root.iconphoto(True, self._window_icon)

        header = tk.Frame(self.root, bg=BG, padx=24, pady=20)
        header.pack(fill="x")
        logo = tk.Canvas(
            header, width=46, height=46, bg=ACCENT_DARK, highlightthickness=0
        )
        logo.create_oval(17, 8, 29, 29, fill=ACCENT, outline="")
        logo.create_arc(11, 17, 35, 38, start=0, extent=-180, style="arc", outline=TEXT, width=3)
        logo.create_line(23, 36, 23, 41, fill=TEXT, width=3)
        logo.create_line(17, 41, 29, 41, fill=TEXT, width=3)
        logo.pack(side="left", padx=(0, 14))
        titles = tk.Frame(header, bg=BG)
        titles.pack(side="left")
        tk.Label(
            titles,
            text="Nova Voice Pilot",
            bg=BG,
            fg=TEXT,
            font=("Segoe UI Variable Display", 20, "bold"),
        ).pack(anchor="w")
        tk.Label(
            titles,
            text="Private voice control, running entirely on this PC",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(2, 0))

        status_chip = tk.Frame(
            header,
            bg=SURFACE_2,
            padx=12,
            pady=7,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        status_chip.pack(side="right")
        self.status_dot = tk.Canvas(
            status_chip, width=10, height=10, bg=SURFACE_2, highlightthickness=0
        )
        self.status_dot.create_oval(1, 1, 9, 9, fill=ACCENT, outline="", tags="dot")
        self.status_dot.pack(side="left", padx=(0, 7))
        self.status_var = tk.StringVar(value="Starting locally…")
        tk.Label(
            status_chip,
            textvariable=self.status_var,
            bg=SURFACE_2,
            fg=TEXT,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left")

        overview = tk.Frame(self.root, bg=BG, padx=24)
        overview.pack(fill="x")
        listener_card = self._card(overview)
        listener_card.pack(side="left", fill="both", expand=True, padx=(0, 8))
        tk.Label(
            listener_card,
            text="LISTENING MODE",
            bg=SURFACE,
            fg=MUTED,
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w")
        self.mode_var = tk.StringVar(value="Loading configuration…")
        tk.Label(
            listener_card,
            textvariable=self.mode_var,
            bg=SURFACE,
            fg=TEXT,
            font=("Segoe UI", 13, "bold"),
        ).pack(anchor="w", pady=(7, 3))
        self.mode_help_var = tk.StringVar(value="The microphone will initialize shortly.")
        tk.Label(
            listener_card,
            textvariable=self.mode_help_var,
            bg=SURFACE,
            fg=MUTED,
            font=("Segoe UI", 9),
            wraplength=350,
            justify="left",
        ).pack(anchor="w")

        safety_card = self._card(overview)
        safety_card.pack(side="left", fill="both", expand=True, padx=(8, 0))
        tk.Label(
            safety_card,
            text="LOCAL SAFETY",
            bg=SURFACE,
            fg=MUTED,
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w")

        device_card = self._card(self.root)
        device_card.pack(fill="x", padx=24, pady=(16, 0))
        device_title = tk.Frame(device_card, bg=SURFACE)
        device_title.pack(fill="x", pady=(0, 10))
        tk.Label(
            device_title,
            text="AUDIO DEVICES",
            bg=SURFACE,
            fg=MUTED,
            font=("Segoe UI", 8, "bold"),
        ).pack(side="left")
        tk.Label(
            device_title,
            text="Saved selections are restored on every launch",
            bg=SURFACE,
            fg=MUTED,
            font=("Segoe UI", 8),
        ).pack(side="right")

        device_grid = tk.Frame(device_card, bg=SURFACE)
        device_grid.pack(fill="x")
        device_grid.grid_columnconfigure(0, weight=1)
        device_grid.grid_columnconfigure(1, weight=1)
        tk.Label(
            device_grid,
            text="Input microphone",
            bg=SURFACE,
            fg=TEXT,
            font=("Segoe UI", 9, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        tk.Label(
            device_grid,
            text="Output speaker",
            bg=SURFACE,
            fg=TEXT,
            font=("Segoe UI", 9, "bold"),
        ).grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.input_device_var = tk.StringVar(value="System default")
        self.output_device_var = tk.StringVar(value="System default")
        self.input_device_box = ttk.Combobox(
            device_grid,
            textvariable=self.input_device_var,
            state="readonly",
            style="Dark.TCombobox",
            font=("Segoe UI", 9),
        )
        self.input_device_box.grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(6, 0))
        self.output_device_box = ttk.Combobox(
            device_grid,
            textvariable=self.output_device_var,
            state="readonly",
            style="Dark.TCombobox",
            font=("Segoe UI", 9),
        )
        self.output_device_box.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(6, 0))

        meter_header = tk.Frame(device_card, bg=SURFACE)
        meter_header.pack(fill="x", pady=(14, 5))
        tk.Label(
            meter_header,
            text="LIVE MICROPHONE LEVEL",
            bg=SURFACE,
            fg=TEXT,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left")
        self.meter_value_var = tk.StringVar(value="Mic −72.0 dBFS  •  Threshold −50.5 dBFS")
        tk.Label(
            meter_header,
            textvariable=self.meter_value_var,
            bg=SURFACE,
            fg=MUTED,
            font=("Segoe UI", 8),
        ).pack(side="right")
        self.mic_meter = tk.Canvas(
            device_card,
            height=30,
            bg="#0B1118",
            highlightbackground=BORDER,
            highlightthickness=1,
            cursor="sb_h_double_arrow",
        )
        self.mic_meter.pack(fill="x")
        self.mic_meter.create_rectangle(0, 0, 0, 0, fill=ACCENT, outline="", tags="level")
        self.mic_meter.create_line(0, 0, 0, 0, fill=TEXT, width=3, tags="threshold")
        self.mic_meter.bind("<Configure>", lambda _event: self._draw_microphone_meter())
        self.mic_meter.bind("<Button-1>", self._begin_threshold_drag)
        self.mic_meter.bind("<B1-Motion>", self._drag_threshold)
        self.mic_meter.bind("<ButtonRelease-1>", self._finish_threshold_drag)
        threshold_help = tk.Frame(device_card, bg=SURFACE)
        threshold_help.pack(fill="x", pady=(5, 0))
        tk.Label(
            threshold_help,
            text="Drag the white line just above the background-noise level. Audio must cross it to be captured.",
            bg=SURFACE,
            fg=MUTED,
            font=("Segoe UI", 8),
        ).pack(side="left")
        self.threshold_save_var = tk.StringVar(value="")
        tk.Label(
            threshold_help,
            textvariable=self.threshold_save_var,
            bg=SURFACE,
            fg=SUCCESS,
            font=("Segoe UI", 8),
        ).pack(side="right")

        device_actions = tk.Frame(device_card, bg=SURFACE)
        device_actions.pack(fill="x", pady=(10, 0))
        self._button(
            device_actions,
            "Save and apply devices",
            self.save_audio_devices,
            background=ACCENT_DARK,
            foreground=ACCENT,
        ).pack(side="left")
        self._button(
            device_actions, "Refresh device list", self.refresh_audio_devices
        ).pack(side="left", padx=(10, 0))
        self.audio_save_var = tk.StringVar(value="")
        tk.Label(
            device_actions,
            textvariable=self.audio_save_var,
            bg=SURFACE,
            fg=SUCCESS,
            font=("Segoe UI", 8),
        ).pack(side="right")
        tk.Label(
            safety_card,
            text="F9  Emergency stop",
            bg=SURFACE,
            fg=TEXT,
            font=("Segoe UI", 13, "bold"),
        ).pack(anchor="w", pady=(7, 3))
        tk.Label(
            safety_card,
            text="Risky actions still pause for your confirmation.",
            bg=SURFACE,
            fg=MUTED,
            font=("Segoe UI", 9),
            wraplength=350,
            justify="left",
        ).pack(anchor="w")

        controls = tk.Frame(self.root, bg=BG, padx=24, pady=16)
        controls.pack(fill="x")
        self._button(
            controls,
            "Stop current task",
            self.stop_task,
            background=DANGER_DARK,
            foreground=DANGER,
        ).pack(side="left", padx=(0, 10))
        self.listen_toggle_button = self._button(
            controls,
            "Stop listening",
            self.toggle_listening,
            background=ACCENT_DARK,
            foreground=ACCENT,
        )
        self.listen_toggle_button.pack(side="left", padx=(0, 10))
        self._button(controls, "Open audit log", self.open_audit).pack(side="left")
        self._button(
            controls,
            "Turn off assistant",
            self.close,
            background=ACCENT_DARK,
            foreground=ACCENT,
        ).pack(side="right")

        activity = tk.Frame(
            self.root,
            bg=SURFACE,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        activity.pack(fill="both", expand=True, padx=24, pady=(0, 14))
        activity_header = tk.Frame(activity, bg=SURFACE, padx=14, pady=10)
        activity_header.pack(fill="x")
        tk.Label(
            activity_header,
            text="Activity",
            bg=SURFACE,
            fg=TEXT,
            font=("Segoe UI", 11, "bold"),
        ).pack(side="left")
        tk.Label(
            activity_header,
            text="Local logs only",
            bg=SURFACE,
            fg=MUTED,
            font=("Segoe UI", 8),
        ).pack(side="right")

        log_frame = tk.Frame(activity, bg=LOG_BG)
        log_frame.pack(fill="both", expand=True, padx=1, pady=(0, 1))
        scrollbar = tk.Scrollbar(log_frame, bg=SURFACE_2, troughcolor=LOG_BG)
        scrollbar.pack(side="right", fill="y")
        self.log = tk.Text(
            log_frame,
            wrap="word",
            state="disabled",
            font=("Cascadia Mono", 9),
            bg=LOG_BG,
            fg="#C8D5E3",
            insertbackground=TEXT,
            selectbackground="#244B60",
            relief="flat",
            borderwidth=0,
            padx=14,
            pady=12,
            yscrollcommand=scrollbar.set,
        )
        self.log.pack(fill="both", expand=True)
        scrollbar.configure(command=self.log.yview)

        tk.Label(
            self.root,
            text="Audio, transcripts, models, and action logs stay on this computer.",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 8),
        ).pack(pady=(0, 14))

        writer = QueueWriter(self.output)
        sys.stdout = writer
        sys.stderr = writer
        self.worker = threading.Thread(target=self._run_assistant, daemon=True)
        self.worker.start()
        self._start_tray()
        self.root.bind("<Configure>", self._schedule_responsive_scale, add="+")
        self.root.after(100, self._refresh)

    def _set_dark_title_bar(self) -> None:
        if sys.platform != "win32":
            return
        try:
            self.root.update_idletasks()
            enabled = ctypes.c_int(1)
            child_hwnd = self.root.winfo_id()
            user32 = ctypes.WinDLL("user32")
            user32.GetParent.restype = ctypes.c_void_p
            hwnd = user32.GetParent(child_hwnd) or child_hwnd
            dwmapi = ctypes.WinDLL("dwmapi")
            result = dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(enabled), ctypes.sizeof(enabled)
            )
            if result != 0:
                dwmapi.DwmSetWindowAttribute(
                    hwnd, 19, ctypes.byref(enabled), ctypes.sizeof(enabled)
                )
        except (AttributeError, OSError):
            pass

    def _configure_combobox_style(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Dark.TCombobox",
            foreground=TEXT,
            fieldbackground=SURFACE_2,
            background=SURFACE_2,
            arrowcolor=ACCENT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            padding=7,
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", SURFACE_2)],
            foreground=[("readonly", TEXT)],
            selectbackground=[("readonly", SURFACE_2)],
            selectforeground=[("readonly", TEXT)],
        )
        self.root.option_add("*TCombobox*Listbox.background", SURFACE_2)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_DARK)
        self.root.option_add("*TCombobox*Listbox.selectForeground", TEXT)

    @staticmethod
    def _card(parent: tk.Misc) -> tk.Frame:
        return tk.Frame(
            parent,
            bg=SURFACE,
            padx=16,
            pady=14,
            highlightbackground=BORDER,
            highlightthickness=1,
        )

    @staticmethod
    def _button(
        parent: tk.Misc,
        text: str,
        command: Any,
        *,
        background: str = SURFACE_2,
        foreground: str = TEXT,
    ) -> tk.Button:
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=background,
            fg=foreground,
            activebackground=BORDER,
            activeforeground=TEXT,
            relief="flat",
            borderwidth=0,
            padx=16,
            pady=9,
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
        )
        button._normal_bg = background  # type: ignore[attr-defined]
        button.bind("<Enter>", lambda _event: button.configure(bg=BORDER))
        button.bind(
            "<Leave>",
            lambda _event: button.configure(
                bg=getattr(button, "_normal_bg", background)
            ),
        )
        return button

    def _schedule_responsive_scale(self, event: tk.Event[Any]) -> None:
        if event.widget is not self.root:
            return
        if self._scale_after_id is not None:
            self.root.after_cancel(self._scale_after_id)
        self._scale_after_id = self.root.after(120, self._apply_responsive_scale)

    def _apply_responsive_scale(self) -> None:
        self._scale_after_id = None
        width = max(self.root.winfo_width(), 1)
        height = max(self.root.winfo_height(), 1)
        factor = min(max(min(width / 920, height / 700), 0.84), 1.35)
        if abs(factor - self._ui_scale) < 0.04:
            return
        self._ui_scale = factor
        self.root.tk.call("tk", "scaling", self._base_tk_scaling * factor)

    def _run_assistant(self) -> None:
        try:
            self.assistant = VoiceAssistant(load_config())
            if self.shutdown_requested.is_set():
                self.assistant.request_shutdown()
                return
            self.assistant.run()
        except Exception as exc:
            self.output.put(f"Startup error: {exc}\n")
            self.startup_error = str(exc)

    @staticmethod
    def _device_choices(kind: str, saved_key: str) -> tuple[list[str], dict[str, str]]:
        labels = ["System default"]
        mapping = {"System default": ""}
        for device in list_audio_devices(kind):
            label = f"{device.label}  [{device.index}]"
            labels.append(label)
            mapping[label] = device.key
        if saved_key and saved_key not in mapping.values():
            label = f"Unavailable saved device  ·  {saved_key}"
            labels.append(label)
            mapping[label] = saved_key
        return labels, mapping

    def refresh_audio_devices(self) -> None:
        assistant = self.assistant
        if assistant is None:
            self.audio_save_var.set("Assistant is still starting")
            return
        try:
            input_labels, self.input_device_keys = self._device_choices(
                "input", assistant.config.input_audio_device
            )
            output_labels, self.output_device_keys = self._device_choices(
                "output", assistant.config.output_audio_device
            )
        except Exception as exc:
            self.audio_save_var.set(f"Device scan failed: {exc}")
            return
        self.input_device_box.configure(values=input_labels)
        self.output_device_box.configure(values=output_labels)
        input_label = next(
            (label for label, key in self.input_device_keys.items() if key == assistant.config.input_audio_device),
            "System default",
        )
        output_label = next(
            (label for label, key in self.output_device_keys.items() if key == assistant.config.output_audio_device),
            "System default",
        )
        self.input_device_var.set(input_label)
        self.output_device_var.set(output_label)
        self.audio_save_var.set("")

    def save_audio_devices(self) -> None:
        input_key = self.input_device_keys.get(self.input_device_var.get())
        output_key = self.output_device_keys.get(self.output_device_var.get())
        if input_key is None or output_key is None:
            self.audio_save_var.set("Refresh the device list and try again")
            return
        try:
            save_audio_device_settings(
                CONFIG_PATH, input_device=input_key, output_device=output_key
            )
        except (OSError, ValueError) as exc:
            self.audio_save_var.set(f"Could not save: {exc}")
            return
        self.audio_save_var.set("Saved — restarting audio engine…")
        self.restart_pending = True
        if self.assistant is not None:
            self.assistant.request_shutdown()

    def _rms_to_db(self, rms: float) -> float:
        if rms <= 0:
            return self._meter_min_db
        return min(max(20.0 * math.log10(rms), self._meter_min_db), self._meter_max_db)

    @staticmethod
    def _db_to_rms(db: float) -> float:
        return 10.0 ** (db / 20.0)

    def _db_to_meter_x(self, db: float, width: int) -> float:
        usable = max(width - 4, 1)
        ratio = (db - self._meter_min_db) / (self._meter_max_db - self._meter_min_db)
        return 2 + min(max(ratio, 0.0), 1.0) * usable

    def _draw_microphone_meter(self, level_rms: float | None = None) -> None:
        width = max(self.mic_meter.winfo_width(), 5)
        height = max(self.mic_meter.winfo_height(), 5)
        if level_rms is None:
            assistant = self.assistant
            level_rms = assistant.input_level_rms if assistant is not None else 0.0
        level_db = self._rms_to_db(level_rms)
        threshold_db = self._rms_to_db(self._meter_threshold_rms)
        level_x = self._db_to_meter_x(level_db, width)
        threshold_x = self._db_to_meter_x(threshold_db, width)
        level_color = SUCCESS if level_rms >= self._meter_threshold_rms else ACCENT
        self.mic_meter.coords("level", 2, 2, level_x, height - 2)
        self.mic_meter.itemconfigure("level", fill=level_color)
        self.mic_meter.coords("threshold", threshold_x, 1, threshold_x, height - 1)
        state = "capturing" if level_rms >= self._meter_threshold_rms else "waiting"
        self.meter_value_var.set(
            f"Mic {level_db:.1f} dBFS  •  Threshold {threshold_db:.1f} dBFS  •  {state}"
        )

    def _threshold_from_x(self, x: int) -> float:
        width = max(self.mic_meter.winfo_width() - 4, 1)
        ratio = min(max((x - 2) / width, 0.0), 1.0)
        db = self._meter_min_db + ratio * (self._meter_max_db - self._meter_min_db)
        return self._db_to_rms(db)

    def _set_threshold_from_pointer(self, x: int) -> None:
        self._meter_threshold_rms = self._threshold_from_x(x)
        assistant = self.assistant
        if assistant is not None:
            assistant.set_vad_rms_threshold(self._meter_threshold_rms)
        self.threshold_save_var.set("Release to save")
        self._draw_microphone_meter()

    def _begin_threshold_drag(self, event: tk.Event[Any]) -> None:
        self._threshold_dragging = True
        self._set_threshold_from_pointer(event.x)

    def _drag_threshold(self, event: tk.Event[Any]) -> None:
        if self._threshold_dragging:
            self._set_threshold_from_pointer(event.x)

    def _finish_threshold_drag(self, event: tk.Event[Any]) -> None:
        if not self._threshold_dragging:
            return
        self._set_threshold_from_pointer(event.x)
        self._threshold_dragging = False
        try:
            save_vad_threshold_setting(CONFIG_PATH, self._meter_threshold_rms)
        except (OSError, ValueError) as exc:
            self.threshold_save_var.set(f"Could not save: {exc}")
            return
        self.threshold_save_var.set("Threshold saved")

    def _start_tray(self) -> None:
        menu = pystray.Menu(
            pystray.MenuItem("Show dashboard", lambda _icon, _item: self.show()),
            pystray.MenuItem("Stop current task", lambda _icon, _item: self.stop_task()),
            pystray.MenuItem("Turn off", lambda _icon, _item: self.root.after(0, self.close)),
        )
        self.tray = pystray.Icon("local_voice_pilot", app_icon(), "Nova Voice Pilot", menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def show(self) -> None:
        self.root.after(0, self._show_on_ui_thread)

    def _show_on_ui_thread(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def stop_task(self) -> None:
        assistant = self.assistant
        if assistant is not None:
            stopped = assistant.request_stop()
            self.output.put(
                f"\nStopped current task. Terminated {len(stopped)} tracked process tree(s).\n"
            )

    def toggle_listening(self) -> None:
        assistant = self.assistant
        if assistant is None:
            return
        assistant.set_listening_enabled(not assistant.listening_enabled)
        state = "resumed" if assistant.listening_enabled else "paused"
        self.output.put(f"\nListening {state} from the dashboard.\n")

    def open_audit(self) -> None:
        assistant = self.assistant
        if assistant is None:
            return
        try:
            import os

            os.startfile(str(assistant.config.journal_path))
        except OSError as exc:
            messagebox.showerror("Nova Voice Pilot", str(exc), parent=self.root)

    def _refresh(self) -> None:
        while True:
            try:
                value = self.output.get_nowait()
            except queue.Empty:
                break
            self.log.configure(state="normal")
            self.log.insert("end", value)
            self.log.see("end")
            self.log.configure(state="disabled")
        assistant = self.assistant
        if assistant is not None:
            status = assistant.status
            self.status_var.set(status)
            color = SUCCESS if status.startswith(("Ready", "Listening")) else ACCENT
            if status in {"Off", "Stopped current task"}:
                color = DANGER
            self.status_dot.itemconfigure("dot", fill=color)
            if assistant.config.activation_mode == "wake_word":
                phrase = assistant.config.wake_word_phrase
                if assistant.listening_enabled:
                    self.mode_var.set(f'Listening for “{phrase}”')
                    self.mode_help_var.set(
                        f'Start a request with “{phrase}”. Say “{phrase}, stop listening” '
                        "to enter sleep mode."
                    )
                    self.listen_toggle_button.configure(
                        text="Stop listening", bg=ACCENT_DARK, fg=ACCENT
                    )
                    self.listen_toggle_button._normal_bg = ACCENT_DARK  # type: ignore[attr-defined]
                else:
                    self.mode_var.set("Sleep mode")
                    self.mode_help_var.set(
                        f'Normal requests are ignored. Only “{phrase}, start listening” '
                        "is accepted; the microphone remains available for that wake command."
                    )
                    self.listen_toggle_button.configure(
                        text="Start listening", bg=DANGER_DARK, fg=DANGER
                    )
                    self.listen_toggle_button._normal_bg = DANGER_DARK  # type: ignore[attr-defined]
            else:
                key = assistant.config.push_to_talk_key.upper()
                self.mode_var.set(f"Push to talk: {key}")
                self.mode_help_var.set(f"Hold {key} while speaking, then release to process.")
            if self._device_assistant_id != id(assistant):
                self._device_assistant_id = id(assistant)
                self._meter_threshold_rms = assistant.vad_rms_threshold
                self.refresh_audio_devices()
            self._draw_microphone_meter(assistant.input_level_rms)
        elif self.startup_error is not None:
            self.status_var.set("Startup failed")
            self.status_dot.itemconfigure("dot", fill=DANGER)
        if (
            self.restart_pending
            and not self.closing
            and self.worker is not None
            and not self.worker.is_alive()
        ):
            self.assistant = None
            self.startup_error = None
            self._device_assistant_id = None
            self.restart_pending = False
            self.worker = threading.Thread(target=self._run_assistant, daemon=True)
            self.worker.start()
            self.audio_save_var.set("Devices applied")
        if not self.closing:
            self.root.after(100, self._refresh)

    def close(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.shutdown_requested.set()
        self.status_var.set("Turning off…")
        self.status_dot.itemconfigure("dot", fill=DANGER)
        assistant = self.assistant
        if assistant is not None:
            assistant.request_shutdown()
        if self.tray is not None:
            self.tray.stop()
        self.root.after(750, self.root.destroy)

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    if not claim_single_instance():
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(
            "Nova Voice Pilot", "Nova Voice Pilot is already running.", parent=root
        )
        root.destroy()
        return 0
    VoiceAssistantDashboard().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
