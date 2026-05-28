# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P5-S2 Phase 3.1: per-(session, tool) three-state circuit breaker.

Spec: ``openspec/changes/p5-s2-self-healing-harness/specs/tool-registry/circuit-breaker.md``

Each ``(sid, tool_name)`` pair gets its own breaker state machine:

* ``CLOSED``  — normal; counts failures.
* ``OPEN``    — refusing calls; cooldown timer running.
* ``HALF_OPEN`` — single probe allowed; success closes, failure re-opens.

Time is injected via a ``clock`` callable so tests don't sleep. Locks
are ``asyncio.Lock`` (the breaker is touched by both dispatch and the
watchdog from different tasks).
"""
from __future__ import annotations

import pytest

from agent.circuit_breaker import ToolCircuitBreaker


# ─────────────── helpers ───────────────


class _FakeClock:
    """Mutable monotonic clock so tests can advance time without sleeping."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:  # matches ``time.time``-style signature
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ─────────────── tests ───────────────


@pytest.mark.asyncio
async def test_three_consecutive_failures_open() -> None:
    """3 consecutive failures on the same (sid, tool) → state OPEN; the
    4th ``can_call`` returns False (no probing yet — cooldown not done)."""
    clock = _FakeClock()
    cb = ToolCircuitBreaker(threshold=3, cooldown_seconds=60.0, clock=clock)

    # 3 failures.
    for _ in range(3):
        await cb.record_call("A", "write_file", ok=False)

    assert await cb.state("A", "write_file") == "OPEN"
    # 4th can_call refused (still inside cooldown — 0 seconds elapsed).
    assert await cb.can_call("A", "write_file") is False


@pytest.mark.asyncio
async def test_success_resets_failure_count() -> None:
    """2 failures + 1 success → counter cleared, breaker still CLOSED.

    Without this reset semantics, a long-lived session that flutters
    between success and failure would slowly accumulate to 3 and
    spuriously open."""
    cb = ToolCircuitBreaker(threshold=3, cooldown_seconds=60.0)

    await cb.record_call("A", "write_file", ok=False)
    await cb.record_call("A", "write_file", ok=False)
    await cb.record_call("A", "write_file", ok=True)

    assert await cb.state("A", "write_file") == "CLOSED"
    # And another failure is the FIRST failure (not the third).
    await cb.record_call("A", "write_file", ok=False)
    assert await cb.state("A", "write_file") == "CLOSED"
    await cb.record_call("A", "write_file", ok=False)
    assert await cb.state("A", "write_file") == "CLOSED"  # still 2 failures


@pytest.mark.asyncio
async def test_open_to_half_open_after_cooldown() -> None:
    """OPEN + cooldown elapsed → first ``can_call`` returns True ONCE
    (state becomes HALF_OPEN, probe in flight); subsequent ``can_call``
    returns False until the probe resolves."""
    clock = _FakeClock()
    cb = ToolCircuitBreaker(threshold=3, cooldown_seconds=60.0, clock=clock)

    # Open it.
    for _ in range(3):
        await cb.record_call("A", "write_file", ok=False)
    assert await cb.state("A", "write_file") == "OPEN"

    # Just under cooldown — still refused.
    clock.advance(59.0)
    assert await cb.can_call("A", "write_file") is False
    assert await cb.state("A", "write_file") == "OPEN"

    # Past cooldown — first can_call promotes to HALF_OPEN and allows the probe.
    clock.advance(2.0)  # total 61s elapsed
    assert await cb.can_call("A", "write_file") is True
    assert await cb.state("A", "write_file") == "HALF_OPEN"

    # Second can_call before the probe resolves → refused.
    assert await cb.can_call("A", "write_file") is False


@pytest.mark.asyncio
async def test_half_open_success_closes() -> None:
    """HALF_OPEN + probe success → CLOSED + failure_count reset."""
    clock = _FakeClock()
    cb = ToolCircuitBreaker(threshold=3, cooldown_seconds=60.0, clock=clock)

    for _ in range(3):
        await cb.record_call("A", "write_file", ok=False)
    clock.advance(61.0)
    assert await cb.can_call("A", "write_file") is True  # → HALF_OPEN

    await cb.record_call("A", "write_file", ok=True)

    assert await cb.state("A", "write_file") == "CLOSED"
    # And we're allowed to call freely again.
    assert await cb.can_call("A", "write_file") is True


@pytest.mark.asyncio
async def test_half_open_failure_reopens() -> None:
    """HALF_OPEN + probe failure → OPEN immediately (no need for 3 more
    failures). Cooldown timer resets so the probe can't be retried
    instantly."""
    clock = _FakeClock()
    cb = ToolCircuitBreaker(threshold=3, cooldown_seconds=60.0, clock=clock)

    for _ in range(3):
        await cb.record_call("A", "write_file", ok=False)
    clock.advance(61.0)
    assert await cb.can_call("A", "write_file") is True  # HALF_OPEN

    await cb.record_call("A", "write_file", ok=False)

    assert await cb.state("A", "write_file") == "OPEN"
    # Cooldown timer reset — even at +5s we're still refused.
    clock.advance(5.0)
    assert await cb.can_call("A", "write_file") is False


@pytest.mark.asyncio
async def test_per_tool_isolation() -> None:
    """``write_file`` OPEN must not affect ``run_shell`` for the same sid."""
    cb = ToolCircuitBreaker(threshold=3, cooldown_seconds=60.0)

    for _ in range(3):
        await cb.record_call("A", "write_file", ok=False)

    assert await cb.state("A", "write_file") == "OPEN"
    assert await cb.state("A", "run_shell") == "CLOSED"
    assert await cb.can_call("A", "write_file") is False
    assert await cb.can_call("A", "run_shell") is True


@pytest.mark.asyncio
async def test_per_session_isolation() -> None:
    """sid='A' write_file OPEN must not affect sid='B' write_file."""
    cb = ToolCircuitBreaker(threshold=3, cooldown_seconds=60.0)

    for _ in range(3):
        await cb.record_call("A", "write_file", ok=False)

    assert await cb.state("A", "write_file") == "OPEN"
    assert await cb.state("B", "write_file") == "CLOSED"
    assert await cb.can_call("B", "write_file") is True


@pytest.mark.asyncio
async def test_can_call_in_closed_state_does_not_change_state() -> None:
    """Sanity: can_call on a fresh CLOSED breaker is a pure read — must
    NOT auto-promote, must NOT bump any counters."""
    cb = ToolCircuitBreaker(threshold=3, cooldown_seconds=60.0)

    for _ in range(10):
        assert await cb.can_call("A", "write_file") is True
    assert await cb.state("A", "write_file") == "CLOSED"


@pytest.mark.asyncio
async def test_default_threshold_and_cooldown_construct_cleanly() -> None:
    """No-arg construction works (uses spec defaults: threshold=3, cooldown=60)."""
    cb = ToolCircuitBreaker()
    assert await cb.can_call("X", "y") is True
    assert await cb.state("X", "y") == "CLOSED"
