"""P5-S2 Phase 3.1: per-(session, tool) circuit breaker.

Spec: ``openspec/changes/p5-s2-self-healing-harness/specs/tool-registry/circuit-breaker.md``

Three-state machine guards each ``(session_id, tool_name)`` pair so a
runaway LLM that keeps retrying the same broken tool stops costing wall
time and tokens after the third failure.

States::

    CLOSED      ── 3 failures ──▶  OPEN
    OPEN        ── cooldown   ──▶  HALF_OPEN  (allows ONE probe)
    HALF_OPEN   ── success    ──▶  CLOSED
    HALF_OPEN   ── failure    ──▶  OPEN  (cooldown timer resets)

The breaker stores no per-call data; it just owns the small state record
keyed by (sid, tool). Time is injected via ``clock`` for determinism in
tests. ``asyncio.Lock`` protects every transition because the dispatch
path and the watchdog scan loop run on different async tasks.

Why we don't use a library: this is ~80 lines, the corner cases (probe
in flight, cooldown reset on probe failure, per-tuple isolation) are
project-specific, and depending on a 3rd-party breaker would import
threading semantics that don't compose with the agent loop's asyncio
model.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Callable


# ─────────────── data ───────────────


@dataclass
class _BreakerState:
    """Per-(sid, tool) record. Mutated in-place under the breaker lock."""

    state: str = "CLOSED"          # CLOSED | OPEN | HALF_OPEN
    failure_count: int = 0
    opened_at: float = 0.0          # monotonic seconds when state→OPEN
    half_open_in_flight: bool = False  # True between probe-allow and probe-resolve


# ─────────────── breaker ───────────────


class ToolCircuitBreaker:
    """Spec-compliant per-(sid, tool) breaker.

    Construction defaults: ``threshold=3`` consecutive failures opens
    the breaker; ``cooldown_seconds=60`` before HALF_OPEN promotion.
    Pass ``clock=`` to inject a fake time function in tests.
    """

    def __init__(
        self,
        *,
        threshold: int = 3,
        cooldown_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if threshold <= 0:
            raise ValueError(f"threshold must be > 0, got {threshold!r}")
        if cooldown_seconds <= 0:
            raise ValueError(
                f"cooldown_seconds must be > 0, got {cooldown_seconds!r}"
            )
        self._threshold = int(threshold)
        self._cooldown = float(cooldown_seconds)
        self._clock = clock
        self._states: dict[tuple[str, str], _BreakerState] = {}
        self._lock = asyncio.Lock()

    # ─────────── internals ───────────

    def _get_or_create(self, sid: str, tool_name: str) -> _BreakerState:
        """Caller MUST hold ``self._lock``."""
        key = (sid, tool_name)
        st = self._states.get(key)
        if st is None:
            st = _BreakerState()
            self._states[key] = st
        return st

    # ─────────── public API ───────────

    async def can_call(self, sid: str, tool_name: str) -> bool:
        """Return True iff a call is currently allowed.

        Side effects:
          * If state is OPEN and cooldown has elapsed, promotes to
            HALF_OPEN AND returns True (this is the "probe" that gets
            to try once).
          * If state is HALF_OPEN and a probe is in flight, returns
            False — only one concurrent probe is allowed.
          * CLOSED state is a pure read (no counter bump).
        """
        async with self._lock:
            st = self._get_or_create(sid, tool_name)
            if st.state == "CLOSED":
                return True
            if st.state == "OPEN":
                # Cooldown elapsed?
                if (self._clock() - st.opened_at) >= self._cooldown:
                    st.state = "HALF_OPEN"
                    st.half_open_in_flight = True
                    return True
                return False
            # HALF_OPEN
            if st.half_open_in_flight:
                return False
            # No probe in flight (rare — would happen if can_call is
            # invoked twice without a record_call between them).
            st.half_open_in_flight = True
            return True

    async def record_call(self, sid: str, tool_name: str, ok: bool) -> None:
        """Record the outcome of a tool call so the breaker can advance
        its state machine.

        Truth table:
          * CLOSED + success   → counter reset
          * CLOSED + failure   → counter++; threshold-th failure → OPEN
          * HALF_OPEN + success → CLOSED + counter reset
          * HALF_OPEN + failure → OPEN + cooldown timer reset
          * OPEN + (anything)  → ignored (caller shouldn't be running
            tools while OPEN, but be defensive)
        """
        async with self._lock:
            st = self._get_or_create(sid, tool_name)
            if st.state == "OPEN":
                # Defensive: caller dispatched while breaker was OPEN.
                # Don't advance the state machine — they shouldn't have
                # called the tool. Just return.
                return

            if st.state == "HALF_OPEN":
                st.half_open_in_flight = False
                if ok:
                    st.state = "CLOSED"
                    st.failure_count = 0
                else:
                    st.state = "OPEN"
                    st.opened_at = self._clock()
                return

            # CLOSED.
            if ok:
                st.failure_count = 0
                return
            st.failure_count += 1
            if st.failure_count >= self._threshold:
                st.state = "OPEN"
                st.opened_at = self._clock()

    async def state(self, sid: str, tool_name: str) -> str:
        """Return the current state ('CLOSED' | 'OPEN' | 'HALF_OPEN')
        WITHOUT side effects (no auto-promote on cooldown elapsed —
        for that, call ``can_call`` instead)."""
        async with self._lock:
            st = self._states.get((sid, tool_name))
            if st is None:
                return "CLOSED"
            return st.state

    async def cooldown_remaining(self, sid: str, tool_name: str) -> float:
        """How many seconds until OPEN auto-promotes to HALF_OPEN.

        Returns 0.0 for CLOSED / HALF_OPEN / unknown breakers, and 0.0
        for an OPEN breaker whose cooldown already elapsed.
        """
        async with self._lock:
            st = self._states.get((sid, tool_name))
            if st is None or st.state != "OPEN":
                return 0.0
            elapsed = self._clock() - st.opened_at
            return max(0.0, self._cooldown - elapsed)


__all__ = ["ToolCircuitBreaker"]
