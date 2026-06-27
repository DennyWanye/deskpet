# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import re
from dataclasses import dataclass
from threading import RLock
from typing import Optional


@dataclass(frozen=True)
class TaskScopeDecision:
    effective_sid: str
    created: bool
    reason: str
    stripped_text: str
    force_l2_page_in: Optional[str] = None


class TaskSessionManager:
    """In-process task-session scope resolver.

    Explicit ``/new`` creates a deterministic per-base sid using a
    monotonic counter. ``/continue`` stays in the base sid and marks a
    future L2 page-in override without implementing page-in here.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._counters: dict[str, int] = {}
        self._active: dict[str, str] = {}
        self._peer_groups: dict[str, str] = {}

    def resolve(
        self,
        base_sid: str,
        text: str,
        explicit_new: bool,
        force_l2: bool = False,
    ) -> TaskScopeDecision:
        base = base_sid or "default"
        raw_text = text or ""
        stripped_new = self._strip_command(raw_text, "/new")
        stripped_continue = self._strip_command(raw_text, "/continue")
        has_new = stripped_new is not None
        has_continue = stripped_continue is not None

        if explicit_new or has_new:
            body = stripped_new if has_new else raw_text
            with self._lock:
                seq = self._counters.get(base, 0) + 1
                self._counters[base] = seq
                effective_sid = f"task-{self._sid_key(base)}-{seq}"
                self._active[base] = effective_sid
            return TaskScopeDecision(
                effective_sid=effective_sid,
                created=True,
                reason="explicit_new",
                stripped_text=body,
            )

        if force_l2 or has_continue:
            body = stripped_continue if has_continue else raw_text
            return TaskScopeDecision(
                effective_sid=base,
                created=False,
                reason="continue",
                stripped_text=body,
                force_l2_page_in="always",
            )

        return TaskScopeDecision(
            effective_sid=base,
            created=False,
            reason="default",
            stripped_text=raw_text,
        )

    def active_sid(self, base_sid: str) -> str:
        base = base_sid or "default"
        with self._lock:
            return self._active.get(base, base)

    def register_peer(self, transport_sid: str) -> None:
        sid = transport_sid or "default"
        with self._lock:
            self._peer_groups.setdefault(sid, self._initial_peer_group(sid))

    def peer_group(self, transport_sid: str) -> str:
        sid = transport_sid or "default"
        with self._lock:
            return self._peer_groups.get(sid, self._initial_peer_group(sid))

    def remap_peer_group(self, transport_sid: str, effective_sid: str) -> None:
        sid = transport_sid or "default"
        target = effective_sid or sid
        with self._lock:
            old_group = self._peer_groups.get(sid, self._initial_peer_group(sid))
            self._peer_groups.setdefault(sid, old_group)
            for peer, group in list(self._peer_groups.items()):
                if group == old_group:
                    self._peer_groups[peer] = target

    def peers_for_group(self, effective_sid: str) -> list[str]:
        with self._lock:
            return [
                peer
                for peer, group in self._peer_groups.items()
                if group == effective_sid
            ]

    @staticmethod
    def _strip_command(text: str, command: str) -> str | None:
        if text == command:
            return ""
        prefix = command + " "
        if text.startswith(prefix):
            return text[len(prefix):].lstrip()
        return None

    @staticmethod
    def _initial_peer_group(transport_sid: str) -> str:
        return "default" if transport_sid in {"default", "message-panel-main"} else transport_sid

    @staticmethod
    def _sid_key(base_sid: str) -> str:
        key = re.sub(r"[^A-Za-z0-9_-]+", "-", base_sid).strip("-")
        return key or "default"


task_session_manager = TaskSessionManager()

