"""P5-S1: per-session activity tracking for the supervisor watchdog.

Lives next to ``agent_loop.py``. Pure data — no I/O, no LLM. The watchdog
loop reads from this; the WS event forwarder in main.py writes to it.

Design notes:
- in-memory only (rebuilt on backend restart); Code-mode session state
  is durable via the existing ``code_sessions`` table, but supervisor
  watchdog activity restarts cleanly with the process — there's no
  meaningful "carried-forward stuckness".
- protected by ``asyncio.Lock`` because both the WS forwarder and the
  watchdog scan loop touch this from different async tasks.
- ring buffer caps recent_events at 5 to keep memory bounded; signature
  window keeps last-seen-tool-call counts for repeat detection.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("deskpet.agent.session_activity")


# Ring-buffer cap; matches spec "last_5_events".
_RECENT_EVENTS_CAP = 5
# Signature window — only track the last-seen tool-call signature counts;
# resets when a different signature comes in (consecutive-only).
_SIG_RESET_ON_DIFFERENT = True


def args_hash(args: Any) -> str:
    """Stable hash of tool args for repeat-detection.

    JSON-canonicalises (sorted keys, no whitespace) before hashing so
    that ``{"a":1,"b":2}`` and ``{"b":2,"a":1}`` collide. Falls back to
    ``str(args)`` for anything not JSON-serialisable.
    """
    try:
        canon = json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except Exception:
        canon = str(args)
    return hashlib.sha1(canon.encode("utf-8")).hexdigest()[:12]


@dataclass
class CompactEvent:
    """Compact event entry for the recent-events ring buffer.

    Spec: ``{type, ts, name, args_hash, ok, snippet}``. ``ts`` is unix
    seconds (float). ``snippet`` is at most 80 chars to keep snapshots
    cheap to send to the supervisor LLM.
    """

    type: str = ""
    ts: float = 0.0
    name: str = ""
    args_hash: str = ""
    ok: Optional[bool] = None
    snippet: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type, "ts": self.ts}
        if self.name:
            d["name"] = self.name
        if self.args_hash:
            d["args_hash"] = self.args_hash
        if self.ok is not None:
            d["ok"] = self.ok
        if self.snippet:
            d["snippet"] = self.snippet
        return d


@dataclass
class SessionActivity:
    """Per-session activity state. Mutated in-place by ``SessionActivityStore``."""

    session_id: str = ""
    status: str = "running"  # idle | running | permission | error
    last_event_ts: float = field(default_factory=lambda: time.time())
    recent_events: deque = field(default_factory=lambda: deque(maxlen=_RECENT_EVENTS_CAP))
    # tool_signature -> consecutive count
    tool_signature_window: dict[str, int] = field(default_factory=dict)
    current_iteration: int = 0
    max_iterations: int = 50
    # Set True when a chat_v2_error was emitted; cleared by the watchdog
    # when it scans this sid (prevents repeat-firing on the same error).
    error_pending_supervisor: bool = False
    # P5-S2 Phase 4: how many times the AutoResumeOrchestrator has spawned
    # a fresh chat task for this sid since the last user-initiated turn.
    # Reset to 0 by main.py on user message arrival; capped by
    # ``[supervisor].max_auto_resume_attempts`` (default 2).
    auto_resume_attempts: int = 0
    # Last sig signature seen (for "consecutive only" reset).
    _last_sig: str = ""

    def to_snapshot_dict(self) -> dict[str, Any]:
        """Convenience for ``snapshot.build_snapshot``. Does NOT include
        full conversation — that's a deliberate privacy/cost decision.
        """
        return {
            "session_id": self.session_id,
            "status": self.status,
            "last_activity_age_seconds": max(0.0, time.time() - self.last_event_ts),
            "current_iteration": self.current_iteration,
            "max_iterations": self.max_iterations,
            "last_5_events": [e.to_dict() for e in self.recent_events],
            "tool_signature_window": dict(self.tool_signature_window),
            "error_pending": self.error_pending_supervisor,
        }


class SessionActivityStore:
    """Singleton-style holder used by main.py's WS forwarder + watchdog.

    Not a hard singleton — instances are created in main.py at startup
    and registered to ``service_context["session_activity"]``.
    """

    def __init__(self) -> None:
        self._states: dict[str, SessionActivity] = {}
        self._lock = asyncio.Lock()

    # ─── async API used by the WS forwarder ────────────────────────────

    async def bump(
        self,
        sid: str,
        *,
        event_type: str,
        ts: Optional[float] = None,
        name: str = "",
        args: Any = None,
        ok: Optional[bool] = None,
        snippet: str = "",
        iteration: int = 0,
        max_iterations: int = 0,
    ) -> None:
        """Record an AgentEvent for this sid.

        Idempotent on missing sid (auto-creates entry). ``args`` may be
        anything JSON-ish; we only keep its hash for repeat detection.
        """
        ts = ts if ts is not None else time.time()
        async with self._lock:
            sa = self._states.get(sid)
            if sa is None:
                sa = SessionActivity(session_id=sid)
                self._states[sid] = sa
            sa.last_event_ts = ts
            if iteration:
                sa.current_iteration = iteration
            if max_iterations:
                sa.max_iterations = max_iterations
            ah = args_hash(args) if args is not None else ""
            sa.recent_events.append(
                CompactEvent(
                    type=event_type,
                    ts=ts,
                    name=name,
                    args_hash=ah,
                    ok=ok,
                    snippet=(snippet or "")[:80],
                )
            )
            # Tool-signature window: only count consecutive same-signature
            # tool_call events. Anything else resets the window.
            if event_type == "tool_call" and name:
                sig = f"{name}:{ah}"
                if _SIG_RESET_ON_DIFFERENT and sa._last_sig and sa._last_sig != sig:
                    sa.tool_signature_window.clear()
                sa.tool_signature_window[sig] = sa.tool_signature_window.get(sig, 0) + 1
                sa._last_sig = sig
            elif event_type in ("tool_result",):
                # Tool result doesn't reset (the same signature might have
                # 2-3 consecutive tries); but a non-tool event does.
                pass
            else:
                # Reset signature window on assistant message / final / error.
                sa.tool_signature_window.clear()
                sa._last_sig = ""

    async def set_status(self, sid: str, status: str) -> None:
        async with self._lock:
            sa = self._states.get(sid)
            if sa is None:
                sa = SessionActivity(session_id=sid)
                self._states[sid] = sa
            sa.status = status

    async def mark_error_pending(self, sid: str) -> None:
        async with self._lock:
            sa = self._states.get(sid)
            if sa is None:
                sa = SessionActivity(session_id=sid)
                self._states[sid] = sa
            sa.error_pending_supervisor = True
            sa.status = "error"

    async def clear_error_pending(self, sid: str) -> None:
        async with self._lock:
            sa = self._states.get(sid)
            if sa is None:
                return
            sa.error_pending_supervisor = False

    async def drop(self, sid: str) -> None:
        async with self._lock:
            self._states.pop(sid, None)

    # ─── P5-S2 Phase 4: auto-resume attempt counter ───────────────────

    async def increment_auto_resume_attempts(self, sid: str) -> int:
        """Bump the auto-resume attempt counter for sid.

        Returns the new counter value. Auto-creates a SessionActivity
        entry if missing (orchestrator should never lose count just
        because the WS forwarder hasn't seen any events yet).
        """
        async with self._lock:
            sa = self._states.get(sid)
            if sa is None:
                sa = SessionActivity(session_id=sid)
                self._states[sid] = sa
            sa.auto_resume_attempts += 1
            return sa.auto_resume_attempts

    async def reset_auto_resume_attempts(self, sid: str) -> None:
        """Reset the auto-resume attempt counter for sid back to 0.

        Called by main.py when a fresh user chat message arrives — the
        user implicitly granted a new auto-resume budget. No-op when sid
        has no SessionActivity yet.
        """
        async with self._lock:
            sa = self._states.get(sid)
            if sa is not None:
                sa.auto_resume_attempts = 0

    async def get(self, sid: str) -> Optional[SessionActivity]:
        async with self._lock:
            return self._states.get(sid)

    async def all_sids(self) -> list[str]:
        async with self._lock:
            return list(self._states.keys())

    async def snapshot_all(self) -> dict[str, dict[str, Any]]:
        async with self._lock:
            return {sid: sa.to_snapshot_dict() for sid, sa in self._states.items()}


# Module-level singleton — most callers go through service_context, but
# importing this directly is fine for unit tests.
_default_store: Optional[SessionActivityStore] = None


def default_store() -> SessionActivityStore:
    global _default_store
    if _default_store is None:
        _default_store = SessionActivityStore()
    return _default_store
