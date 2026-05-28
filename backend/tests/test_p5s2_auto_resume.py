# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P5-S2 Phase 4: AutoResumeOrchestrator tests.

Spec: ``openspec/changes/p5-s2-self-healing-harness/specs/pet-supervisor/auto-resume.md``

The orchestrator closes the supervisor → main-agent loop:

1. Recoverable error fires (max_iterations / circuit_open / permanent_tool_error / hallucination).
2. Orchestrator calls ``supervisor.diagnose(sid, snapshot)``.
3. If supervisor returns ``action="nudge"``, orchestrator spawns a fresh
   chat task on the same session with the hint injected as a system msg —
   no user input required, up to ``max_attempts`` times.
4. If supervisor returns ``action="ask_user"``, orchestrator falls
   through to the existing P5-S1 popup path (does not auto-spawn).
5. After ``max_attempts`` consecutive auto-resumes for the same sid,
   orchestrator emits ``auto_resume_exhausted`` and stops trying.
6. When ``auto_resume_enabled=false``, orchestrator skips entirely.
7. User new message resets the attempt counter (handled by main.py at
   chat handler entry, not by orchestrator itself).
"""
from __future__ import annotations

import pytest

from agent.auto_resume import AutoResumeOrchestrator, AutoResumeResult
from agent.session_activity import SessionActivityStore
from agent.supervisor import SupervisorAction


# ─────────────── helpers ───────────────


class _RecordingDispatcher:
    """Captures (sid, msgs) tuples each time ``handle_failure`` spawns."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict]]] = []

    async def __call__(self, sid: str, msgs: list[dict]) -> None:
        self.calls.append((sid, list(msgs)))


class _RecordingEmitter:
    """Captures (event_type, payload) ws emissions."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def __call__(self, event_type: str, payload: dict) -> None:
        self.events.append((event_type, dict(payload)))


class _RecordingAudit:
    def __init__(self) -> None:
        self.records: list[dict] = []

    async def __call__(self, hint: dict) -> None:
        self.records.append(dict(hint))


class _StubSupervisor:
    """Returns a pre-canned SupervisorAction; records (sid, snapshot) pairs."""

    def __init__(self, action: SupervisorAction) -> None:
        self._action = action
        self.calls: list[tuple[str, dict]] = []

    async def diagnose(self, sid: str, snapshot=None) -> SupervisorAction:
        self.calls.append((sid, dict(snapshot or {})))
        return self._action


def _nudge_action(hint: str = "试试 edit_file 而不是 write_file") -> SupervisorAction:
    return SupervisorAction(
        action="nudge",
        severity="yellow",
        diagnosis="repeated tool failure",
        hint_for_main_agent=hint,
        user_message="自愈中…",
        suggested_buttons=[],
        alert_id="alert-test-1",
    )


def _ask_user_action() -> SupervisorAction:
    return SupervisorAction(
        action="ask_user",
        severity="red",
        diagnosis="user intervention needed",
        hint_for_main_agent="",
        user_message="需要你确认是否继续",
        suggested_buttons=["继续", "中断"],
        alert_id="alert-test-2",
    )


async def _make_store_with_session(sid: str) -> SessionActivityStore:
    store = SessionActivityStore()
    # bump once so the SessionActivity entry exists
    await store.bump(sid, event_type="assistant_message")
    return store


def _make_orch(
    *,
    supervisor,
    dispatcher,
    activity_store,
    enabled: bool = True,
    max_attempts: int = 2,
    emitter=None,
    audit=None,
) -> AutoResumeOrchestrator:
    return AutoResumeOrchestrator(
        supervisor=supervisor,
        chat_dispatcher=dispatcher,
        activity_store=activity_store,
        max_attempts=max_attempts,
        enabled=enabled,
        ws_emitter=emitter,
        audit_writer=audit,
    )


# ─────────────── 4.1 trigger接入 ───────────────


@pytest.mark.asyncio
async def test_max_iterations_triggers_supervisor():
    sid = "s-max-iter"
    store = await _make_store_with_session(sid)
    sup = _StubSupervisor(_nudge_action())
    disp = _RecordingDispatcher()
    orch = _make_orch(supervisor=sup, dispatcher=disp, activity_store=store)

    snapshot = {"session_id": sid, "current_iteration": 50, "max_iterations": 50}
    msgs = [{"role": "user", "content": "do task"}]
    result = await orch.handle_failure(sid, "max_iterations", snapshot, msgs)

    assert sup.calls and sup.calls[0][0] == sid
    assert result.action == "spawned"
    assert result.attempt == 1


@pytest.mark.asyncio
async def test_circuit_open_triggers_supervisor():
    sid = "s-circuit"
    store = await _make_store_with_session(sid)
    sup = _StubSupervisor(_nudge_action("换 edit_file 试试"))
    disp = _RecordingDispatcher()
    orch = _make_orch(supervisor=sup, dispatcher=disp, activity_store=store)

    snapshot = {"session_id": sid, "tool": "write_file", "error": "circuit_open"}
    result = await orch.handle_failure(sid, "circuit_open", snapshot, [{"role": "user", "content": "x"}])

    assert sup.calls and sup.calls[0][0] == sid
    assert sup.calls[0][1].get("error") == "circuit_open"
    assert result.action == "spawned"


@pytest.mark.asyncio
async def test_permanent_error_triggers_supervisor():
    sid = "s-perm"
    store = await _make_store_with_session(sid)
    sup = _StubSupervisor(_nudge_action())
    disp = _RecordingDispatcher()
    orch = _make_orch(supervisor=sup, dispatcher=disp, activity_store=store)

    snapshot = {"session_id": sid, "tool": "write_file", "detail": "missing required parameter: path"}
    result = await orch.handle_failure(sid, "permanent_tool_error", snapshot, [])
    assert sup.calls
    assert result.action == "spawned"


@pytest.mark.asyncio
async def test_supervisor_action_nudge_spawns_new_task():
    sid = "s-nudge"
    store = await _make_store_with_session(sid)
    hint_text = "改用 edit_file，因为 write_file 已熔断"
    sup = _StubSupervisor(_nudge_action(hint_text))
    disp = _RecordingDispatcher()
    orch = _make_orch(supervisor=sup, dispatcher=disp, activity_store=store)

    original_msgs = [
        {"role": "system", "content": "you are helpful"},
        {"role": "user", "content": "create file"},
    ]
    result = await orch.handle_failure(sid, "max_iterations", {"session_id": sid}, original_msgs)

    # dispatcher 被调用一次，sid 正确
    assert len(disp.calls) == 1
    spawn_sid, spawn_msgs = disp.calls[0]
    assert spawn_sid == sid
    # hint 在新 _msgs 末尾作为 system msg 注入
    last_msg = spawn_msgs[-1]
    assert last_msg.get("role") == "system"
    assert hint_text in last_msg.get("content", "")
    # 原 msgs 仍保留
    assert spawn_msgs[0] == original_msgs[0]
    assert any(m.get("content") == "create file" for m in spawn_msgs)
    # attempts 计数到 1
    sa = await store.get(sid)
    assert sa is not None
    assert sa.auto_resume_attempts == 1
    assert result.action == "spawned"
    assert result.attempt == 1


@pytest.mark.asyncio
async def test_supervisor_action_ask_user_does_not_auto_spawn():
    sid = "s-ask"
    store = await _make_store_with_session(sid)
    sup = _StubSupervisor(_ask_user_action())
    disp = _RecordingDispatcher()
    orch = _make_orch(supervisor=sup, dispatcher=disp, activity_store=store)

    result = await orch.handle_failure(sid, "permanent_tool_error", {"session_id": sid}, [])

    # 没 dispatch
    assert disp.calls == []
    # attempts 不增
    sa = await store.get(sid)
    assert sa is not None
    assert sa.auto_resume_attempts == 0
    assert result.action == "ask_user"


@pytest.mark.asyncio
async def test_max_attempts_caps_resume():
    sid = "s-cap"
    store = await _make_store_with_session(sid)
    sup = _StubSupervisor(_nudge_action())
    disp = _RecordingDispatcher()
    emitter = _RecordingEmitter()
    orch = _make_orch(
        supervisor=sup, dispatcher=disp, activity_store=store,
        max_attempts=2, emitter=emitter,
    )

    # 1st attempt — spawn
    r1 = await orch.handle_failure(sid, "max_iterations", {"session_id": sid}, [])
    assert r1.action == "spawned"
    # 2nd attempt — spawn
    r2 = await orch.handle_failure(sid, "max_iterations", {"session_id": sid}, [])
    assert r2.action == "spawned"
    # 3rd attempt — exhausted, no supervisor call, no dispatch
    sup.calls.clear()
    disp.calls.clear()
    r3 = await orch.handle_failure(sid, "max_iterations", {"session_id": sid, "detail": "still broken"}, [])
    assert r3.action == "exhausted"
    assert sup.calls == []
    assert disp.calls == []
    # exhausted ws event emitted
    assert any(ev[0] == "auto_resume_exhausted" for ev in emitter.events)


@pytest.mark.asyncio
async def test_session_disabled_blocks_resume():
    sid = "s-disabled"
    store = await _make_store_with_session(sid)
    sup = _StubSupervisor(_nudge_action())
    disp = _RecordingDispatcher()
    orch = _make_orch(supervisor=sup, dispatcher=disp, activity_store=store, enabled=False)

    result = await orch.handle_failure(sid, "max_iterations", {"session_id": sid}, [])

    assert result.action == "ask_user"
    assert result.reason == "disabled"
    assert sup.calls == []
    assert disp.calls == []
    sa = await store.get(sid)
    assert sa is not None and sa.auto_resume_attempts == 0


# ─────────────── 4.3 ws 事件 ───────────────


@pytest.mark.asyncio
async def test_emits_auto_resume_started_ws_event():
    sid = "s-started"
    store = await _make_store_with_session(sid)
    sup = _StubSupervisor(_nudge_action("hint X"))
    disp = _RecordingDispatcher()
    emitter = _RecordingEmitter()
    orch = _make_orch(
        supervisor=sup, dispatcher=disp, activity_store=store, emitter=emitter,
    )

    await orch.handle_failure(sid, "max_iterations", {"session_id": sid}, [])

    started = [ev for ev in emitter.events if ev[0] == "auto_resume_started"]
    assert len(started) == 1
    payload = started[0][1]
    assert payload["session_id"] == sid
    assert payload["attempt"] == 1
    assert "hint X" in payload.get("hint_preview", "")


@pytest.mark.asyncio
async def test_emits_auto_resume_succeeded_when_final():
    """Per spec: succeeded 由 main.py 在新 task FinalEvent 时 emit，不是 orchestrator。

    This test documents the contract: orchestrator does NOT auto-emit
    succeeded — main.py owns that side because only it sees FinalEvent.
    """
    sid = "s-success"
    store = await _make_store_with_session(sid)
    sup = _StubSupervisor(_nudge_action())
    disp = _RecordingDispatcher()
    emitter = _RecordingEmitter()
    orch = _make_orch(
        supervisor=sup, dispatcher=disp, activity_store=store, emitter=emitter,
    )

    await orch.handle_failure(sid, "max_iterations", {"session_id": sid}, [])

    # orchestrator emits started but NOT succeeded
    assert any(ev[0] == "auto_resume_started" for ev in emitter.events)
    assert not any(ev[0] == "auto_resume_succeeded" for ev in emitter.events)


@pytest.mark.asyncio
async def test_emits_auto_resume_exhausted_after_max_attempts():
    sid = "s-exhausted"
    store = await _make_store_with_session(sid)
    sup = _StubSupervisor(_nudge_action())
    disp = _RecordingDispatcher()
    emitter = _RecordingEmitter()
    orch = _make_orch(
        supervisor=sup, dispatcher=disp, activity_store=store,
        max_attempts=1, emitter=emitter,
    )

    # 1st spawn — attempts go to 1
    await orch.handle_failure(sid, "max_iterations", {"session_id": sid}, [])
    # 2nd attempt — exhausted (max_attempts=1)
    emitter.events.clear()
    await orch.handle_failure(
        sid, "permanent_tool_error",
        {"session_id": sid, "detail": "boom"}, [],
    )

    exhausted = [ev for ev in emitter.events if ev[0] == "auto_resume_exhausted"]
    assert len(exhausted) == 1
    payload = exhausted[0][1]
    assert payload["session_id"] == sid
    assert payload["attempts"] == 1
    assert "final_error" in payload


# ─────────────── audit ───────────────


@pytest.mark.asyncio
async def test_audit_recorded_on_spawn():
    sid = "s-audit"
    store = await _make_store_with_session(sid)
    sup = _StubSupervisor(_nudge_action("audit hint"))
    disp = _RecordingDispatcher()
    audit = _RecordingAudit()
    orch = _make_orch(
        supervisor=sup, dispatcher=disp, activity_store=store, audit=audit,
    )

    await orch.handle_failure(sid, "max_iterations", {"session_id": sid}, [])

    assert len(audit.records) == 1
    rec = audit.records[0]
    assert rec["session_id"] == sid
    assert rec["action"] == "auto_resumed"
    assert "audit hint" in rec.get("hint_text", "")
    assert rec.get("alert_id")  # non-empty


# ─────────────── reset on user message ───────────────


@pytest.mark.asyncio
async def test_session_activity_reset_auto_resume_attempts():
    """SessionActivityStore exposes reset method used by main.py at user
    chat handler entry — verifies the API contract."""
    sid = "s-reset"
    store = await _make_store_with_session(sid)
    # bump it via increment (orchestrator's path)
    await store.increment_auto_resume_attempts(sid)
    await store.increment_auto_resume_attempts(sid)
    sa = await store.get(sid)
    assert sa.auto_resume_attempts == 2
    # reset
    await store.reset_auto_resume_attempts(sid)
    sa = await store.get(sid)
    assert sa.auto_resume_attempts == 0
