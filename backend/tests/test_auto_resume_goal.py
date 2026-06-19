# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""FP-2 Task 4: AutoResumeOrchestrator goal_text_getter (WI-1.5 narrow version).

Tests that the orchestrator injects a goal anchor message BEFORE the
supervisor hint when a ``goal_text_getter`` callable is provided and
returns a non-None string for the given session id.

BC: when getter is None or returns None, new_msgs must contain ONLY the
supervisor hint — byte-identical to pre-WI-1.5 behavior.
"""
from __future__ import annotations

import pytest

from agent.auto_resume import AutoResumeOrchestrator, AutoResumeResult
from agent.session_activity import SessionActivityStore
from agent.supervisor import SupervisorAction


# ─────────────── helpers (reused from test_p5s2_auto_resume patterns) ─────────

class _RecordingDispatcher:
    """Captures (sid, msgs) on each dispatch call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict]]] = []

    async def __call__(self, sid: str, msgs: list[dict]) -> None:
        self.calls.append((sid, list(msgs)))


class _StubSupervisor:
    def __init__(self, action: SupervisorAction) -> None:
        self._action = action

    async def diagnose(self, sid: str, snapshot=None) -> SupervisorAction:
        return self._action


def _nudge_action(hint: str = "HINT") -> SupervisorAction:
    return SupervisorAction(
        action="nudge",
        severity="yellow",
        diagnosis="test",
        hint_for_main_agent=hint,
        user_message="自愈中…",
        suggested_buttons=[],
        alert_id="alert-goal-test",
    )


async def _make_store(sid: str) -> SessionActivityStore:
    store = SessionActivityStore()
    await store.bump(sid, event_type="assistant_message")
    return store


def _make_orch(
    *,
    supervisor,
    dispatcher,
    activity_store,
    goal_text_getter=None,
) -> AutoResumeOrchestrator:
    return AutoResumeOrchestrator(
        supervisor=supervisor,
        chat_dispatcher=dispatcher,
        activity_store=activity_store,
        goal_text_getter=goal_text_getter,
    )


# ─────────────── tests ───────────────


@pytest.mark.asyncio
async def test_resume_injects_goal_anchor():
    """getter returns "整理纪要" → goal anchor inserted BEFORE supervisor hint."""
    sid = "s-goal-anchor"
    store = await _make_store(sid)
    sup = _StubSupervisor(_nudge_action("HINT"))
    disp = _RecordingDispatcher()

    def getter(session_id: str) -> str | None:
        return "整理纪要"

    orch = _make_orch(
        supervisor=sup,
        dispatcher=disp,
        activity_store=store,
        goal_text_getter=getter,
    )

    original_msgs = [{"role": "user", "content": "start task"}]
    result = await orch.handle_failure(
        sid, "max_iterations", {"session_id": sid}, original_msgs
    )

    assert result.action == "spawned"
    assert len(disp.calls) == 1
    _, new_msgs = disp.calls[0]

    # Find goal anchor and supervisor hint in new_msgs (excluding original_msgs)
    injected = [m for m in new_msgs if m.get("_is_goal_anchor") or m.get("_is_supervisor_hint")]

    assert len(injected) == 2, f"Expected 2 injected msgs, got {len(injected)}: {injected}"

    goal_msg = injected[0]
    hint_msg = injected[1]

    # Goal anchor is first
    assert goal_msg.get("_is_goal_anchor") is True
    assert "整理纪要" in goal_msg.get("content", ""), f"Goal text missing from: {goal_msg}"
    assert goal_msg.get("role") == "system"

    # Supervisor hint is second
    assert hint_msg.get("_is_supervisor_hint") is True
    assert "HINT" in hint_msg.get("content", ""), f"Hint text missing from: {hint_msg}"
    assert hint_msg.get("role") == "system"

    # Goal anchor precedes hint in the full list
    goal_idx = new_msgs.index(goal_msg)
    hint_idx = new_msgs.index(hint_msg)
    assert goal_idx < hint_idx, "goal anchor must come BEFORE supervisor hint"


@pytest.mark.asyncio
async def test_resume_bc_no_goal():
    """getter=None → new_msgs contains ONLY the supervisor hint (BC preserved)."""
    sid = "s-bc-no-goal"
    store = await _make_store(sid)
    sup = _StubSupervisor(_nudge_action("HINT"))
    disp = _RecordingDispatcher()

    orch = _make_orch(
        supervisor=sup,
        dispatcher=disp,
        activity_store=store,
        goal_text_getter=None,  # explicitly None
    )

    original_msgs = [{"role": "user", "content": "do work"}]
    result = await orch.handle_failure(
        sid, "max_iterations", {"session_id": sid}, original_msgs
    )

    assert result.action == "spawned"
    assert len(disp.calls) == 1
    _, new_msgs = disp.calls[0]

    # No goal anchor injected
    goal_msgs = [m for m in new_msgs if m.get("_is_goal_anchor")]
    assert goal_msgs == [], f"No goal anchor expected, got: {goal_msgs}"

    # Exactly one supervisor hint
    hint_msgs = [m for m in new_msgs if m.get("_is_supervisor_hint")]
    assert len(hint_msgs) == 1
    assert "HINT" in hint_msgs[0].get("content", "")


@pytest.mark.asyncio
async def test_resume_bc_getter_returns_none():
    """getter provided but returns None → same as no getter (BC preserved)."""
    sid = "s-bc-getter-none"
    store = await _make_store(sid)
    sup = _StubSupervisor(_nudge_action("HINT"))
    disp = _RecordingDispatcher()

    def getter_returns_none(session_id: str) -> str | None:
        return None

    orch = _make_orch(
        supervisor=sup,
        dispatcher=disp,
        activity_store=store,
        goal_text_getter=getter_returns_none,
    )

    original_msgs = [{"role": "user", "content": "do work"}]
    result = await orch.handle_failure(
        sid, "max_iterations", {"session_id": sid}, original_msgs
    )

    assert result.action == "spawned"
    assert len(disp.calls) == 1
    _, new_msgs = disp.calls[0]

    # No goal anchor injected
    goal_msgs = [m for m in new_msgs if m.get("_is_goal_anchor")]
    assert goal_msgs == [], f"No goal anchor expected, got: {goal_msgs}"

    # Exactly one supervisor hint
    hint_msgs = [m for m in new_msgs if m.get("_is_supervisor_hint")]
    assert len(hint_msgs) == 1
    assert "HINT" in hint_msgs[0].get("content", "")
