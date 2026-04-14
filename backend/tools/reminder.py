"""In-process reminder scheduler (V5 §9).

Allows the LLM to set a note-to-self: ``<tool>reminder</tool>`` (free-text
argument parsing arrives in Phase 2 ReAct; for now the tool simply lists
active reminders, which the LLM can then read before drafting a reply).

State lives in an in-memory list — reminders vanish with the process. When
the semantic memory layer arrives we can persist these into the same
SQLite store and expose a scheduling hook.

This tool is side-effecting (writes state) but low-risk — it cannot delete
files, network, or execute code. ``requires_confirmation=False``.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock

from tools.base import Tool, ToolSpec


@dataclass
class _ReminderStore:
    items: list[tuple[float, str]] = field(default_factory=list)
    lock: Lock = field(default_factory=Lock)

    def add(self, text: str) -> None:
        with self.lock:
            self.items.append((time.time(), text))

    def snapshot(self) -> list[tuple[float, str]]:
        with self.lock:
            return list(self.items)


_store = _ReminderStore()


def add_reminder(text: str) -> None:
    """Module-level helper — lets Python callers seed reminders in tests or
    from other agent layers without going through the Tool protocol."""
    _store.add(text)


class ListRemindersTool:
    """Returns all active reminders as a newline-delimited bullet list."""

    spec = ToolSpec(
        name="list_reminders",
        description=(
            "Returns the user's active reminders. Each line is `HH:MM: text`. "
            "Empty string means no reminders."
        ),
    )

    async def invoke(self, **kwargs: object) -> str:
        items = _store.snapshot()
        if not items:
            return ""
        lines = []
        for ts, text in items:
            hhmm = time.strftime("%H:%M", time.localtime(ts))
            lines.append(f"{hhmm}: {text}")
        return "\n".join(lines)


list_reminders_tool: Tool = ListRemindersTool()


def _reset_for_testing() -> None:
    """Wipe the module-level store. Only for unit tests."""
    with _store.lock:
        _store.items.clear()
