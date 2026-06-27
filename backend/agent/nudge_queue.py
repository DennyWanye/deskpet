# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P5-S4: NudgeQueue — supervisor hint injection queue.

When the supervisor LLM decides ``action=nudge``, it pushes a hint here.
The hint is consumed at the message-build time of the next chat task
(user-initiated retry OR supervisor follow-up), where it becomes a
system message at the top of the system stack.

Design properties (per spec D5):
  * Per-session FIFO with ``cap=3`` — pushes beyond cap drop oldest
  * ``asyncio.Lock`` protects all reads/writes (concurrent push/pop safe)
  * ``pop_all`` is take-and-clear (semantics: "consume on use")
  * ``clear(sid)`` for explicit cleanup on Code-mode exit
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("deskpet.agent.nudge_queue")


_DEFAULT_CAP = 3


@dataclass
class Hint:
    """One supervisor hint awaiting injection.

    ``alert_id`` correlates with the supervisor_alert event that
    originated the hint, so the audit log can be queried back.
    """

    text: str = ""
    alert_id: str = ""
    ts: float = 0.0
    severity: str = "yellow"


class NudgeQueue:
    """Per-session queue with cap + asyncio.Lock + clear()."""

    def __init__(self, *, cap: int = _DEFAULT_CAP) -> None:
        self._cap = max(1, int(cap))
        self._pending: dict[str, list[Hint]] = {}
        self._lock = asyncio.Lock()

    async def push(self, sid: str, hint: Hint) -> None:
        """Append hint to the sid's queue, dropping oldest when at cap."""
        if not hint.ts:
            hint.ts = time.time()
        async with self._lock:
            q = self._pending.setdefault(sid, [])
            if len(q) >= self._cap:
                dropped = q.pop(0)
                logger.info(
                    "nudge_queue_overflow sid=%s dropped_alert=%s",
                    sid,
                    dropped.alert_id or "?",
                )
            q.append(hint)

    async def pop_all(self, sid: str) -> list[Hint]:
        """Take-and-clear: returns all queued hints for sid; empty after."""
        async with self._lock:
            return self._pending.pop(sid, [])

    async def peek(self, sid: str) -> bool:
        """True iff this sid has at least one queued hint. Non-consuming."""
        async with self._lock:
            return bool(self._pending.get(sid))

    async def clear(self, sid: Optional[str] = None) -> None:
        """Clear one sid's queue (or all sids when sid is None)."""
        async with self._lock:
            if sid is None:
                self._pending.clear()
            else:
                self._pending.pop(sid, None)

    async def size(self, sid: str) -> int:
        async with self._lock:
            return len(self._pending.get(sid, []))


def format_hints_for_injection(hints: list[Hint]) -> str:
    """Render a list of Hints as a single ``[Supervisor]`` system message.

    Multi-hint format keeps each on its own bulleted line; single-hint
    flattens to a one-liner. Prefix is fixed so downstream tooling can
    recognize supervisor hints by string match if needed (we also tag
    the message with metadata when pushed into _msgs).
    """
    if not hints:
        return ""
    if len(hints) == 1:
        return f"[Supervisor] {hints[0].text}"
    lines = ["[Supervisor]"]
    for h in hints:
        lines.append(f"- {h.text}")
    return "\n".join(lines)
