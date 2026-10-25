"""Manual, non-destructive smoke test for the Windows UI Automation layer.

It opens a new unsaved Notepad window, inserts one labelled line, verifies that the
document is readable through UI Automation, and asks the local vision planner for a
no-op completion plan. It deliberately leaves the unsaved window open for inspection.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import win32gui
from pywinauto import Desktop

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gui_agent import GUIAgent, WindowObserver


def notepad_handles() -> set[int]:
    handles: set[int] = set()
    for window in Desktop(backend="uia").windows():
        try:
            if window.element_info.process_id and "notepad" in window.window_text().casefold():
                handles.add(int(window.handle))
        except Exception:
            continue
    return handles


def main() -> int:
    before = notepad_handles()
    target = None
    foreground = int(win32gui.GetForegroundWindow())
    for window in Desktop(backend="uia").windows():
        try:
            if int(window.handle) == foreground and "notepad" in window.window_text().casefold():
                target = window
                break
        except Exception:
            continue
    if target is None:
        subprocess.Popen([r"C:\Windows\System32\notepad.exe"], shell=False)
    deadline = time.monotonic() + 10
    while target is None and time.monotonic() < deadline:
        for window in Desktop(backend="uia").windows():
            try:
                if int(window.handle) not in before and "notepad" in window.window_text().casefold():
                    target = window
                    break
            except Exception:
                continue
        if target is not None:
            break
        time.sleep(0.2)
    if target is None:
        raise RuntimeError("A new Notepad window did not appear")

    target.set_focus()
    time.sleep(0.3)
    observer = WindowObserver()
    agent = GUIAgent(
        "http://127.0.0.1:11434/api/chat",
        "local-gui-vision:latest",
        observer=observer,
        stop_requested=lambda: False,
    )
    result = agent.dictate("LOCAL ASSISTANT SMOKE TEST — unsaved.\n")
    if result.status != "done":
        raise RuntimeError(result.message)

    observed = observer.observe(expected_hwnd=int(target.handle))
    documents = [item for item in observed.elements if item.control_type in {"Edit", "Document"}]
    if len(documents) != 1:
        raise RuntimeError(f"Expected one Notepad document, found {len(documents)}")
    value = documents[0].wrapper.window_text()
    if "LOCAL ASSISTANT SMOKE TEST" not in value:
        raise RuntimeError("The inserted text was not visible through UI Automation")

    planned = agent.run_task(
        "In the focused Notepad document, append a new line containing exactly: "
        "GUI PLANNER ACTION TEST"
    )
    if planned.status != "done":
        raise RuntimeError(planned.message)
    final_observation = observer.observe(expected_hwnd=int(target.handle))
    final_documents = [
        item for item in final_observation.elements if item.control_type in {"Edit", "Document"}
    ]
    if len(final_documents) != 1 or "GUI PLANNER ACTION TEST" not in final_documents[0].value:
        raise RuntimeError("The model-planned text was not visible through UI Automation")
    print(f"window={observed.title!r} process={observed.process_name}")
    print(f"dictation={result.status} planner={planned.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
