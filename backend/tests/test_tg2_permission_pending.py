# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-TG-2: PermissionGate read-only pending registry tests.

The ApprovalCenterPanel aggregates in-flight permission prompts. The gate
exposes them via ``list_pending()`` — a pure observability surface that must
NOT alter the gate's decision path. These tests assert:

  1. An in-flight prompt appears in ``list_pending()`` while awaiting the
     user, and is cleared after the responder resolves.
  2. ``list_pending()`` is empty after a timeout auto-deny (no leak).
  3. ``session_id`` filtering returns only that session's pending requests.
  4. The pending registry never changes the decision (deny stays deny).
"""
from __future__ import annotations

import asyncio

import pytest

from deskpet.permissions.gate import PermissionGate, PermissionGateConfig
from deskpet.types.skill_platform import PermissionResponse


@pytest.mark.asyncio
async def test_in_flight_request_listed_then_cleared() -> None:
    """A prompt is visible in list_pending while awaiting, gone afterwards."""
    gate = PermissionGate(config=PermissionGateConfig(timeout_s=5.0))

    started = asyncio.Event()
    release = asyncio.Event()
    seen_during: list[dict] = []

    async def responder(req) -> PermissionResponse:
        # Snapshot the pending registry while the gate is blocked on us.
        started.set()
        await release.wait()
        seen_during.extend(gate.list_pending())
        return PermissionResponse(request_id=req.request_id, decision="allow")

    gate.set_responder(responder)

    task = asyncio.create_task(
        gate.check("shell", {"command": "echo hi"}, session_id="s1")
    )
    await asyncio.wait_for(started.wait(), timeout=2.0)

    # While the responder is blocked, the request is in-flight.
    pending = gate.list_pending()
    assert len(pending) == 1
    entry = pending[0]
    assert entry["category"] == "shell"
    assert entry["session_id"] == "s1"
    assert entry["dangerous"] is True
    assert "request_id" in entry

    release.set()
    decision = await asyncio.wait_for(task, timeout=2.0)

    # Decision path unchanged: user allowed → allow.
    assert decision.allow is True
    # Registry cleared after resolution.
    assert gate.list_pending() == []
    # The responder also saw exactly one entry mid-flight.
    assert len(seen_during) == 1


@pytest.mark.asyncio
async def test_pending_cleared_on_timeout() -> None:
    """Timeout auto-deny must not leak a stale pending entry."""
    gate = PermissionGate(config=PermissionGateConfig(timeout_s=0.2))

    async def slow_responder(req) -> PermissionResponse:
        await asyncio.sleep(5.0)  # never returns before timeout
        return PermissionResponse(request_id=req.request_id, decision="allow")

    gate.set_responder(slow_responder)

    decision = await gate.check(
        "shell", {"command": "sleep"}, session_id="s1"
    )
    assert decision.allow is False
    assert decision.source == "timeout"
    assert gate.list_pending() == []


@pytest.mark.asyncio
async def test_list_pending_session_filter() -> None:
    """session_id filter returns only that session's in-flight requests."""
    gate = PermissionGate(config=PermissionGateConfig(timeout_s=5.0))

    barriers: dict[str, asyncio.Event] = {}
    release = asyncio.Event()

    async def responder(req) -> PermissionResponse:
        ev = barriers.setdefault(req.session_id, asyncio.Event())
        ev.set()
        await release.wait()
        return PermissionResponse(request_id=req.request_id, decision="deny")

    gate.set_responder(responder)

    t1 = asyncio.create_task(
        gate.check("shell", {"command": "a"}, session_id="sA")
    )
    t2 = asyncio.create_task(
        gate.check("shell", {"command": "b"}, session_id="sB")
    )
    # Wait for both responders to be in-flight.
    for sid in ("sA", "sB"):
        for _ in range(50):
            if sid in barriers and barriers[sid].is_set():
                break
            await asyncio.sleep(0.02)

    assert len(gate.list_pending()) == 2
    only_a = gate.list_pending(session_id="sA")
    assert len(only_a) == 1
    assert only_a[0]["session_id"] == "sA"

    release.set()
    d1, d2 = await asyncio.gather(t1, t2)
    # Decision path unchanged: user denied → deny.
    assert d1.allow is False and d2.allow is False
    assert gate.list_pending() == []
