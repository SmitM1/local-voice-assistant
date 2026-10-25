from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class JournalEntry:
    entry_id: str
    timestamp: str
    action: str
    details: dict[str, Any]
    undo: dict[str, Any] | None


class ActionJournal:
    """Append-only local audit log with reversible action metadata."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _append(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def record(
        self,
        action: str,
        details: dict[str, Any],
        undo: dict[str, Any] | None = None,
    ) -> str:
        entry_id = uuid.uuid4().hex
        self._append(
            {
                "event": "action",
                "id": entry_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": action,
                "details": details,
                "undo": undo,
            }
        )
        return entry_id

    def record_event(self, event: str, details: dict[str, Any]) -> None:
        self._append(
            {
                "event": event,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "details": details,
            }
        )

    def latest_reversible(self) -> JournalEntry | None:
        if not self.path.is_file():
            return None
        undone: set[str] = set()
        actions: list[JournalEntry] = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        for line in lines:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("event") == "undo" and isinstance(item.get("action_id"), str):
                undone.add(item["action_id"])
            elif item.get("event") == "action" and isinstance(item.get("undo"), dict):
                actions.append(
                    JournalEntry(
                        entry_id=str(item.get("id", "")),
                        timestamp=str(item.get("timestamp", "")),
                        action=str(item.get("action", "")),
                        details=dict(item.get("details") or {}),
                        undo=dict(item["undo"]),
                    )
                )
        for entry in reversed(actions):
            if entry.entry_id and entry.entry_id not in undone:
                return entry
        return None

    def mark_undone(self, entry: JournalEntry) -> None:
        self._append(
            {
                "event": "undo",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action_id": entry.entry_id,
                "action": entry.action,
            }
        )

    def start_plan(self, summary: str, steps: int) -> str:
        plan_id = uuid.uuid4().hex
        self._append(
            {
                "event": "plan_started",
                "id": plan_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "summary": summary,
                "steps": steps,
            }
        )
        return plan_id

    def finish_plan(
        self,
        plan_id: str,
        *,
        status: str,
        completed_steps: int,
        reversible_action_ids: list[str],
    ) -> None:
        self._append(
            {
                "event": "plan_finished",
                "id": plan_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": status,
                "completed_steps": completed_steps,
                "reversible_action_ids": reversible_action_ids,
            }
        )

    def record_plan_progress(
        self,
        plan_id: str,
        *,
        completed_steps: int,
        reversible_action_ids: list[str],
    ) -> None:
        self._append(
            {
                "event": "plan_progress",
                "id": plan_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "completed_steps": completed_steps,
                "reversible_action_ids": reversible_action_ids,
            }
        )

    def latest_incomplete_plan(self) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        started: dict[str, dict[str, Any]] = {}
        finished: set[str] = set()
        progress: dict[str, dict[str, Any]] = {}
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        for line in lines:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("event") == "plan_started" and item.get("id"):
                started[str(item["id"])] = item
            elif item.get("event") == "plan_finished" and item.get("id"):
                finished.add(str(item["id"]))
            elif item.get("event") == "plan_progress" and item.get("id"):
                progress[str(item["id"])] = item
        for plan_id, item in reversed(tuple(started.items())):
            if plan_id not in finished:
                recovered = dict(item)
                recovered.update(progress.get(plan_id, {}))
                recovered["id"] = plan_id
                return recovered
        return None
