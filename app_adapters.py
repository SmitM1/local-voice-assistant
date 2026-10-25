from __future__ import annotations

import time
from dataclasses import dataclass

from gui_agent import (
    ElementSignature,
    GUIPlan,
    GUIResult,
    PendingAction,
    SAFE_TEXT_CONTROLS,
    WindowObserver,
)


@dataclass(frozen=True)
class AppInspection:
    adapter: str
    process_name: str
    title: str
    summary: str
    items: tuple[str, ...]


class SemanticAppAdapters:
    """Small semantic adapters over fresh UI Automation observations."""

    def __init__(self, observer: WindowObserver, confirmation_timeout_seconds: int) -> None:
        self.observer = observer
        self.confirmation_timeout_seconds = confirmation_timeout_seconds

    def inspect_foreground(self) -> AppInspection:
        observation = self.observer.observe()
        process = observation.process_name
        if process == "notepad.exe":
            documents = [
                item for item in observation.elements if item.control_type in SAFE_TEXT_CONTROLS
            ]
            text = documents[0].value if len(documents) == 1 else ""
            return AppInspection(
                "notepad",
                process,
                observation.title,
                f"Notepad document with {len(text)} visible characters.",
                (text,) if text else (),
            )
        if process == "explorer.exe":
            items = tuple(
                item.name
                for item in observation.elements
                if item.control_type in {"ListItem", "TreeItem"} and item.name
            )
            return AppInspection(
                "explorer",
                process,
                observation.title,
                f"Explorer shows {len(items)} accessible items.",
                items,
            )
        if process == "code.exe":
            items = tuple(
                item.name or item.value
                for item in observation.elements
                if item.control_type in {"Document", "Edit", "TabItem"}
                and (item.name or item.value)
            )
            return AppInspection(
                "vscode",
                process,
                observation.title,
                f"VS Code exposes {len(items)} accessible editor or tab items.",
                items,
            )
        raise ValueError(f"No semantic adapter is available for {process}")

    def prepare_replace_document(self, text: str) -> GUIResult:
        observation = self.observer.observe()
        if observation.process_name not in {"notepad.exe", "code.exe"}:
            return GUIResult(
                "blocked", "Complete-document replacement is supported in Notepad and VS Code only."
            )
        candidates = [
            item
            for item in observation.elements
            if item.control_type in SAFE_TEXT_CONTROLS
            and item.visible
            and item.enabled
            and not item.is_password
        ]
        if len(candidates) != 1:
            return GUIResult(
                "need_user", "Focus a document with one accessible editor, then try again."
            )
        target = candidates[0]
        action = GUIPlan(
            status="action",
            action_type="replace_text",
            element_id=target.element_id,
            text=text,
            reason="Replace the complete document using the semantic application adapter.",
            spoken_response="Document replaced.",
        )
        pending = PendingAction(
            hwnd=observation.hwnd,
            title=observation.title,
            process_name=observation.process_name,
            action=action,
            summary="the complete document contents will be replaced",
            expires_at=time.monotonic() + self.confirmation_timeout_seconds,
            element_signature=ElementSignature.from_element(target),
        )
        return GUIResult(
            "confirmation_required",
            "The complete document will be replaced. Say confirm or cancel.",
            pending,
        )
