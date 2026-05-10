"""P5-S2: SupervisorAgent + parsing + dispatch tests."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agent.supervisor import (
    SupervisorAgent,
    SupervisorAction,
    _parse_action,
    _strip_json_fences,
)
from agent.nudge_queue import NudgeQueue, Hint


# ───────── parser tests ────────────────────────────────────────────


def test_strip_json_fences_with_lang():
    raw = "```json\n{\"action\": \"wait\"}\n```"
    assert _strip_json_fences(raw) == '{"action": "wait"}'


def test_strip_json_fences_plain_object():
    raw = '{"action": "nudge"}'
    assert _strip_json_fences(raw) == raw


def test_strip_json_fences_empty():
    assert _strip_json_fences("") == ""


def test_parse_action_wait():
    raw = '{"action":"wait","severity":"green","diagnosis":"still working"}'
    a = _parse_action(raw, alert_id="x")
    assert a.action == "wait"
    assert a.severity == "green"
    assert a.diagnosis == "still working"
    assert a.user_message == ""
    assert a.suggested_buttons == []


def test_parse_action_nudge():
    raw = json.dumps({
        "action": "nudge",
        "severity": "yellow",
        "diagnosis": "looping on bash",
        "hint_for_main_agent": "switch pip mirror",
        "user_message": "let it switch sources",
        "suggested_buttons": ["go", "stop"],
    })
    a = _parse_action(raw, alert_id="x")
    assert a.action == "nudge"
    assert a.hint_for_main_agent == "switch pip mirror"
    assert a.user_message == "let it switch sources"
    assert a.suggested_buttons == ["go", "stop"]


def test_parse_action_cancel_coerced_to_ask_user():
    raw = json.dumps({"action": "cancel", "severity": "red", "diagnosis": "dead"})
    a = _parse_action(raw, alert_id="x")
    assert a.action == "ask_user"
    assert a.raw_action == "cancel"
    # Default user_message + buttons populated
    assert a.user_message
    assert len(a.suggested_buttons) == 2


def test_parse_action_invalid_json_falls_back_to_ask_user():
    """P5-S1 D fix: parse failure surfaces ask_user fallback so user
    sees the supervisor noticed even though it couldn't decide."""
    a = _parse_action("not json at all", alert_id="x")
    assert a.action == "ask_user"
    assert a.severity == "yellow"
    assert "invalid_json" in a.diagnosis or "parse_failed" in a.diagnosis
    assert a.user_message
    assert len(a.suggested_buttons) == 2


def test_parse_action_handles_thinking_mode_prefix():
    """deepseek-v4-pro and similar models prepend <think>...</think>
    chain-of-thought before the actual JSON. The parser must strip it."""
    raw = '<think>looking at the snapshot... agent seems stuck on bash_run loop</think>{"action":"nudge","severity":"yellow","diagnosis":"loop","hint_for_main_agent":"swap mirror","user_message":"hint","suggested_buttons":["go"]}'
    a = _parse_action(raw, alert_id="x")
    assert a.action == "nudge"
    assert a.hint_for_main_agent == "swap mirror"


def test_parse_action_unknown_action():
    raw = '{"action":"explode","severity":"red"}'
    a = _parse_action(raw, alert_id="x")
    assert a.action == "wait"


def test_parse_action_invalid_severity_clamped():
    raw = '{"action":"nudge","severity":"crimson","diagnosis":"x","hint_for_main_agent":"y"}'
    a = _parse_action(raw, alert_id="x")
    assert a.severity == "green"


def test_parse_action_clamps_lengths():
    long_diag = "a" * 500
    raw = json.dumps({
        "action": "ask_user",
        "severity": "red",
        "diagnosis": long_diag,
        "user_message": "b" * 500,
        "suggested_buttons": ["c" * 100, "d" * 100, "extra"],
    })
    a = _parse_action(raw, alert_id="x")
    assert len(a.diagnosis) <= 200
    assert len(a.user_message) <= 120
    assert len(a.suggested_buttons) == 2  # cap=2
    assert all(len(b) <= 24 for b in a.suggested_buttons)


# ───────── agent dispatch tests ─────────────────────────────────────


class _FakeProvider:
    """Mock provider that returns a canned response or raises on demand."""

    def __init__(self, *, response: dict | None = None, raise_exc: Exception | None = None, delay: float = 0.0):
        self.response = response or {"content": '{"action":"wait","severity":"green","diagnosis":"ok"}'}
        self.raise_exc = raise_exc
        self.delay = delay
        self.calls: list[list[dict]] = []

    async def chat_with_tools(self, messages, *, tools=None, max_tokens=2048, temperature=None):
        self.calls.append(messages)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raise_exc:
            raise self.raise_exc
        return dict(self.response)


async def _stub_snapshot_builder(sid: str) -> dict[str, Any]:
    return {
        "session_id": sid,
        "status": "running",
        "last_activity_age_seconds": 1000,
        "last_5_events": [],
        "tool_signature_window": {},
    }


async def _empty_snapshot_builder(sid: str) -> dict[str, Any]:
    return {}


@pytest.mark.asyncio
async def test_diagnose_wait_no_dispatch():
    nq = NudgeQueue()
    pushed: list = []

    async def push(sid, hint):
        pushed.append((sid, hint))

    broadcast: list = []

    async def bc(typ, payload):
        broadcast.append((typ, payload))

    audit: list = []

    async def audit_fn(action, sid):
        audit.append((sid, action.action))

    agent = SupervisorAgent(
        provider=_FakeProvider(response={"content": '{"action":"wait","severity":"green"}'}),
        snapshot_builder=_stub_snapshot_builder,
        nudge_queue_push=push,
        broadcast=bc,
        audit=audit_fn,
    )
    a = await agent.diagnose("sid-A")
    assert a.action == "wait"
    # NO side effects on wait
    assert pushed == []
    assert broadcast == []
    assert audit == []


@pytest.mark.asyncio
async def test_diagnose_nudge_full_dispatch():
    raw = json.dumps({
        "action": "nudge",
        "severity": "yellow",
        "diagnosis": "looping",
        "hint_for_main_agent": "swap mirror",
        "user_message": "i'll nudge it",
        "suggested_buttons": ["go", "stop"],
    })
    nq = NudgeQueue()
    pushed: list = []
    broadcast: list = []
    audit: list = []

    async def push(sid, h):
        pushed.append((sid, h))

    async def bc(typ, p):
        broadcast.append((typ, p))

    async def audit_fn(act, sid):
        audit.append((sid, act.action))

    agent = SupervisorAgent(
        provider=_FakeProvider(response={"content": raw}),
        snapshot_builder=_stub_snapshot_builder,
        nudge_queue_push=push,
        broadcast=bc,
        audit=audit_fn,
    )
    a = await agent.diagnose("sid-A")
    assert a.action == "nudge"
    assert len(pushed) == 1
    assert pushed[0][0] == "sid-A"
    assert pushed[0][1].hint_for_main_agent == "swap mirror"
    # Broadcast fired with right payload
    assert len(broadcast) == 1
    assert broadcast[0][0] == "supervisor_alert"
    assert broadcast[0][1]["session_id"] == "sid-A"
    assert broadcast[0][1]["action"] == "nudge"
    # Audit fired
    assert audit == [("sid-A", "nudge")]


@pytest.mark.asyncio
async def test_diagnose_llm_timeout_surfaces_to_user():
    """P5-S1 D fix: supervisor LLM timeout no longer silently waits.
    It surfaces an ``ask_user`` so the user sees the supervisor noticed
    and can decide whether to interrupt the stuck task."""
    agent = SupervisorAgent(
        provider=_FakeProvider(delay=2.0),
        snapshot_builder=_stub_snapshot_builder,
        timeout_seconds=0.05,
    )
    a = await agent.diagnose("sid-A")
    assert a.action == "ask_user"
    assert a.severity == "yellow"
    assert "timeout" in a.diagnosis.lower()
    assert a.user_message  # has bubble text
    assert len(a.suggested_buttons) == 2


@pytest.mark.asyncio
async def test_diagnose_llm_error_surfaces_to_user():
    """Same as timeout: any provider exception now → ask_user fallback."""
    agent = SupervisorAgent(
        provider=_FakeProvider(raise_exc=RuntimeError("boom")),
        snapshot_builder=_stub_snapshot_builder,
    )
    a = await agent.diagnose("sid-A")
    assert a.action == "ask_user"
    assert a.severity == "yellow"
    assert "supervisor_unavailable" in a.diagnosis
    assert a.user_message


@pytest.mark.asyncio
async def test_diagnose_empty_snapshot_skips():
    pushed: list = []

    async def push(sid, h):
        pushed.append((sid, h))

    agent = SupervisorAgent(
        provider=_FakeProvider(),
        snapshot_builder=_empty_snapshot_builder,
        nudge_queue_push=push,
    )
    a = await agent.diagnose("sid-A")
    assert a.action == "wait"
    assert "empty" in a.diagnosis.lower()
    assert pushed == []
