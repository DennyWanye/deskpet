"""P5-S2 Phase 7 — supervisor max_iterations rescue + agent_loop self-check.

Two new mechanisms:

1. agent_loop injects a system "self-check" message every N iterations
   so the LLM has to pause + commit-or-continue. Catches the
   "productive-looking but circular" failure where each tool call has
   different args.

2. supervisor.diagnose, when called with a snapshot whose ``reason`` is
   ``max_iterations``, post-processes a wishy-washy ask_user / wait
   into a concrete ``nudge`` with a "stop and commit" hint. This
   unblocks AutoResumeOrchestrator (which only auto-spawns on nudge).
"""
from __future__ import annotations

import asyncio
import json

import pytest

from agent.supervisor import SupervisorAgent, SupervisorAction


# ─── Helpers ─────────────────────────────────────────────────────────


class _FakeProvider:
    def __init__(self, content: str):
        self._content = content

    async def chat_with_tools(self, messages, *, tools=None, max_tokens=2048, temperature=None):
        return {"content": self._content}


async def _stub_snap_with_reason(sid: str) -> dict:
    return {"session_id": sid, "reason": "max_iterations", "detail": "x", "iteration": 50}


# ─── Tests for max_iterations rescue ────────────────────────────────


@pytest.mark.asyncio
async def test_max_iter_rescues_ask_user_to_nudge():
    """When LLM returned ask_user but reason=max_iterations, rescue
    rewrites to nudge with 'stop and commit' hint."""
    raw = json.dumps({
        "action": "ask_user",
        "severity": "yellow",
        "diagnosis": "agent might still be working",
        "user_message": "continue?",
        "suggested_buttons": ["yes", "no"],
    })
    broadcasts = []

    async def bc(typ, payload):
        broadcasts.append((typ, payload))

    pushed = []

    async def push(sid, action):
        pushed.append((sid, action))

    snap = {"session_id": "sid-A", "reason": "max_iterations", "detail": "x", "iteration": 50}
    agent = SupervisorAgent(
        provider=_FakeProvider(raw),
        snapshot_builder=lambda s: _stub_snap_with_reason(s),
        nudge_queue_push=push,
        broadcast=bc,
    )
    a = await agent.diagnose("sid-A", snapshot=snap)
    assert a.action == "nudge"
    assert a.severity == "yellow"
    assert "stop_reason=end_turn" in a.hint_for_main_agent
    assert "强制收尾" in a.diagnosis
    # broadcast should have fired the rescued action
    assert any(t == "supervisor_alert" and p["action"] == "nudge" for t, p in broadcasts)
    # nudge_queue should have received the hint
    assert pushed and pushed[0][0] == "sid-A"
    assert pushed[0][1].action == "nudge"


@pytest.mark.asyncio
async def test_max_iter_rescues_wait_to_nudge():
    """Even if LLM returned wait (overly conservative), rescue forces nudge
    when reason=max_iterations."""
    raw = json.dumps({
        "action": "wait",
        "severity": "green",
        "diagnosis": "still working",
    })
    snap = {"session_id": "sid-B", "reason": "max_iterations", "detail": "x", "iteration": 50}
    agent = SupervisorAgent(
        provider=_FakeProvider(raw),
        snapshot_builder=lambda s: _stub_snap_with_reason(s),
    )
    a = await agent.diagnose("sid-B", snapshot=snap)
    assert a.action == "nudge"


@pytest.mark.asyncio
async def test_max_iter_rescue_does_not_override_existing_nudge():
    """If LLM already returned a nudge, don't rewrite it."""
    raw = json.dumps({
        "action": "nudge",
        "severity": "yellow",
        "diagnosis": "real diag",
        "hint_for_main_agent": "real hint from LLM",
        "user_message": "msg",
        "suggested_buttons": ["a"],
    })
    snap = {"session_id": "sid-C", "reason": "max_iterations", "detail": "x", "iteration": 50}
    agent = SupervisorAgent(
        provider=_FakeProvider(raw),
        snapshot_builder=lambda s: _stub_snap_with_reason(s),
    )
    a = await agent.diagnose("sid-C", snapshot=snap)
    assert a.action == "nudge"
    # LLM's original hint preserved (not overridden by rescue)
    assert a.hint_for_main_agent == "real hint from LLM"


@pytest.mark.asyncio
async def test_other_reasons_do_not_trigger_rescue():
    """Rescue only fires on reason=max_iterations, not other reasons."""
    raw = json.dumps({
        "action": "ask_user",
        "severity": "yellow",
        "diagnosis": "x",
        "user_message": "continue?",
    })
    snap = {"session_id": "sid-D", "reason": "permanent_tool_error", "detail": "x", "iteration": 5}
    agent = SupervisorAgent(
        provider=_FakeProvider(raw),
        snapshot_builder=lambda s: _stub_snap_with_reason(s),
    )
    a = await agent.diagnose("sid-D", snapshot=snap)
    assert a.action == "ask_user"  # NOT rescued


@pytest.mark.asyncio
async def test_no_reason_field_does_not_trigger_rescue():
    """Snapshot without reason field (e.g. watchdog scan) doesn't trigger rescue."""
    raw = json.dumps({
        "action": "ask_user",
        "severity": "yellow",
        "diagnosis": "x",
        "user_message": "continue?",
    })
    snap = {"session_id": "sid-E", "status": "running"}  # no reason
    agent = SupervisorAgent(
        provider=_FakeProvider(raw),
        snapshot_builder=lambda s: _stub_snap_with_reason(s),
    )
    a = await agent.diagnose("sid-E", snapshot=snap)
    assert a.action == "ask_user"


# ─── Tests for in-loop self-check (agent_loop) ──────────────────────


def test_selfcheck_constants_loaded():
    """Lightweight check that the escalating self-check builder works."""
    from agent.agent_loop import (
        _SELFCHECK_EVERY,
        _SELFCHECK_TIER2_AT,
        _SELFCHECK_TIER3_AT,
        _build_selfcheck_message,
    )
    assert _SELFCHECK_EVERY == 10
    assert _SELFCHECK_TIER2_AT == 20
    assert _SELFCHECK_TIER3_AT == 30
    # Tier 1 at iter=10
    msg1 = _build_selfcheck_message(10, 50, 5)
    assert "stop_reason=end_turn" in msg1
    # Tier 2 at iter=20 — should be more forceful
    msg2 = _build_selfcheck_message(20, 50, 15)
    assert "必须" in msg2 or "warning" in msg2.lower() or "警告" in msg2
    # Tier 3 at iter=30 — should be hardest
    msg3 = _build_selfcheck_message(30, 50, 25)
    assert "STRICT STOP" in msg3 or "禁止" in msg3
    assert "stop_reason=end_turn" in msg3


def test_selfcheck_tier_escalation():
    from agent.agent_loop import _build_selfcheck_message
    # Tier 1 / 2 / 3 messages should differ
    m1 = _build_selfcheck_message(10, 50, 1)
    m2 = _build_selfcheck_message(20, 50, 1)
    m3 = _build_selfcheck_message(35, 50, 1)
    assert m1 != m2
    assert m2 != m3
