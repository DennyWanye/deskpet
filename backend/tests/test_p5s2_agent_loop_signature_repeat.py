"""P5-S2 Phase 3.3: same-(name, args) repeat detection in agent loop.

When the LLM keeps invoking the exact same ``(tool_name, args)`` triple
3+ times in a 5-step window, calling the tool again won't change the
result. The loop SHOULD inject a system message reminding the LLM to
look at the existing ``hint`` (or change tactics / try a different tool)
INSTEAD of dispatching the tool a 3rd time.

Key contract:

* Detection runs BEFORE dispatch (so the 3rd identical call's handler
  never fires).
* The system message is appended to the conversation so the LLM sees
  it on the *next* iteration.
* The signature window data structure already exists on
  ``SessionActivity.tool_signature_window`` (P5-S1) — we reuse it via
  the optional ``activity_store`` injected on AgentLoop construction.
* The nudge template is exposed as a module constant so this test can
  assert its substring without copy-pasting the whole string.
"""
from __future__ import annotations

from typing import Any

import pytest

from agent.agent_loop import (
    AgentLoop,
    AssistantMessageEvent,
    ErrorEvent,
    FinalEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from agent.session_activity import SessionActivityStore, args_hash
from llm.types import ChatResponse, ChatUsage, ToolCall


# ─────────────── stubs ───────────────


class _ScriptedLLM:
    def __init__(self, responses: list[ChatResponse]) -> None:
        self._responses = list(responses)
        self.call_count = 0
        self.last_messages: list[dict[str, Any]] | None = None

    async def chat_with_fallback(
        self,
        messages: list[dict[str, Any]],
        **_: Any,
    ) -> ChatResponse:
        self.call_count += 1
        self.last_messages = list(messages)
        if not self._responses:
            raise AssertionError(
                f"ScriptedLLM exhausted after {self.call_count} calls"
            )
        return self._responses.pop(0)


class _ScriptedTools:
    def __init__(self, envelopes: list[dict[str, Any]]) -> None:
        self._envelopes = list(envelopes)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def schemas(self, enabled_toolsets: Any = None) -> list[dict]:  # noqa: ARG002
        return []

    async def execute_tool(
        self,
        name: str,
        params: dict[str, Any],
        session_id: str,  # noqa: ARG002
        task_id: str,  # noqa: ARG002
    ) -> dict[str, Any]:
        self.calls.append((name, dict(params)))
        if not self._envelopes:
            raise AssertionError(
                f"ScriptedTools exhausted after {len(self.calls)} calls — "
                "loop did not stop the repeat call"
            )
        return self._envelopes.pop(0)


def _resp_with_tool_call(
    tc_id: str,
    tool_name: str = "write_file",
    args: dict | None = None,
) -> ChatResponse:
    return ChatResponse(
        content="",
        tool_calls=[
            ToolCall(id=tc_id, name=tool_name, arguments=args or {"path": "/sys/foo"})
        ],
        stop_reason="tool_use",
        model="stub",
        usage=ChatUsage(input_tokens=1, output_tokens=1),
    )


def _envelope_transient_error() -> dict[str, Any]:
    """Transient → loop normally would keep ReAct'ing. Want to make
    sure repeat detection (not permanent-break) fires."""
    return {"ok": False, "result": None, "error": "timeout"}


# ─────────────── helper: pre-seed activity store ───────────────


async def _seed_signature_window(
    store: SessionActivityStore, sid: str, tool_name: str, args: dict, count: int
) -> None:
    """Pre-record N consecutive tool_call events of the same (name, args)
    so the activity store reports ``count`` for that signature."""
    for _ in range(count):
        await store.bump(
            sid,
            event_type="tool_call",
            name=tool_name,
            args=args,
            ok=None,
        )


# ─────────────── tests ───────────────


@pytest.mark.asyncio
async def test_third_identical_call_skipped_and_nudge_injected() -> None:
    """LLM calls (write_file, {path:/sys/foo}) for the 3rd time in a row.

    Expected: handler NOT invoked again, a system msg containing the
    nudge template substring is appended to the conversation.
    """
    from agent.agent_loop import _REPEAT_NUDGE_MSG  # noqa: PLC0415

    store = SessionActivityStore()
    sid = "sRep"
    repeat_args = {"path": "/sys/foo"}
    # Pre-seed: 2 prior identical calls already happened (P5-S1 forwarder
    # would have recorded them as the LLM dispatched in earlier turns).
    await _seed_signature_window(store, sid, "write_file", repeat_args, 2)

    llm = _ScriptedLLM([
        # Turn 1: LLM emits the THIRD identical call → must be intercepted.
        _resp_with_tool_call("c3", args=repeat_args),
        # Turn 2: LLM (after seeing the nudge) gives up cleanly.
        ChatResponse(
            content="OK, I'll stop trying that.",
            tool_calls=[],
            stop_reason="end_turn",
            model="stub",
            usage=ChatUsage(input_tokens=1, output_tokens=1),
        ),
    ])
    # Empty envelopes list — if the loop dispatched, ScriptedTools raises.
    tools = _ScriptedTools([])

    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=tools,
        max_iterations=10,
        activity_store=store,
    )

    events = [
        ev async for ev in loop.run(
            [{"role": "user", "content": "do it"}],
            session_id=sid,
        )
    ]

    # Critical: handler was NOT called.
    assert tools.calls == [], f"handler ran despite repeat detection: {tools.calls!r}"

    # 2 LLM iterations: first emitted the repeat → intercepted, second
    # gave up after seeing the nudge.
    assert llm.call_count == 2

    # The nudge system message lands in the conversation BEFORE the
    # 2nd LLM call.
    assert llm.last_messages is not None
    sys_msgs = [m for m in llm.last_messages if m.get("role") == "system"]
    assert any(
        _REPEAT_NUDGE_MSG.split("{")[0] in (m.get("content") or "")
        or "重复" in (m.get("content") or "")
        for m in sys_msgs
    ), f"no repeat-nudge system message found: {sys_msgs!r}"

    # FinalEvent should still be emitted (the LLM gave up cleanly turn 2).
    assert any(isinstance(e, FinalEvent) for e in events)


@pytest.mark.asyncio
async def test_two_identical_calls_NOT_blocked() -> None:
    """Threshold is 3 — the 2nd identical call is still allowed."""
    store = SessionActivityStore()
    sid = "sLow"
    repeat_args = {"path": "/sys/foo"}
    # Pre-seed: only 1 prior identical call (this incoming one is the 2nd).
    await _seed_signature_window(store, sid, "write_file", repeat_args, 1)

    llm = _ScriptedLLM([
        _resp_with_tool_call("c2", args=repeat_args),
        ChatResponse(
            content="done",
            tool_calls=[],
            stop_reason="end_turn",
            model="stub",
            usage=ChatUsage(input_tokens=1, output_tokens=1),
        ),
    ])
    tools = _ScriptedTools([_envelope_transient_error()])

    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=tools,
        max_iterations=10,
        activity_store=store,
    )

    _ = [ev async for ev in loop.run(
        [{"role": "user", "content": "do it"}],
        session_id=sid,
    )]

    # Handler was called exactly once (this turn's 1 dispatch).
    assert len(tools.calls) == 1


@pytest.mark.asyncio
async def test_different_args_resets_window() -> None:
    """If LLM calls write_file twice with /sys/foo then once with /tmp/x,
    /tmp/x is a fresh signature → not blocked even if the prior 2
    /sys/foo's are in the window. Sanity check that the activity store's
    consecutive-only semantics flow through to the agent loop."""
    store = SessionActivityStore()
    sid = "sDiff"
    # Pre-seed 2 calls with one set of args.
    await _seed_signature_window(store, sid, "write_file", {"path": "/sys/foo"}, 2)

    # Now the LLM calls with DIFFERENT args.
    llm = _ScriptedLLM([
        _resp_with_tool_call("c1", args={"path": "/tmp/x"}),
        ChatResponse(
            content="done",
            tool_calls=[],
            stop_reason="end_turn",
            model="stub",
            usage=ChatUsage(input_tokens=1, output_tokens=1),
        ),
    ])
    tools = _ScriptedTools([{"ok": True, "result": '"ok"', "error": None}])

    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=tools,
        max_iterations=10,
        activity_store=store,
    )

    _ = [ev async for ev in loop.run(
        [{"role": "user", "content": "do it"}],
        session_id=sid,
    )]

    # Different args → handler ran.
    assert len(tools.calls) == 1
    assert tools.calls[0][1] == {"path": "/tmp/x"}


@pytest.mark.asyncio
async def test_nudge_template_is_module_constant() -> None:
    """Verify the constant exists and contains a {name} placeholder so
    other code can reformat it. This pins the contract for future use
    (Phase 4 orchestrator may want to surface the same text to the user)."""
    from agent.agent_loop import _REPEAT_NUDGE_MSG  # noqa: PLC0415

    assert isinstance(_REPEAT_NUDGE_MSG, str)
    assert "{name}" in _REPEAT_NUDGE_MSG
    assert "{count}" in _REPEAT_NUDGE_MSG


@pytest.mark.asyncio
async def test_no_activity_store_means_no_repeat_detection() -> None:
    """Backward compat: if AgentLoop is constructed without
    ``activity_store``, the repeat-detection branch is skipped entirely
    (same behavior as pre-Phase-3 loop). Existing tests don't pass an
    activity_store and must keep working."""
    llm = _ScriptedLLM([
        _resp_with_tool_call("c1"),
        ChatResponse(
            content="done",
            tool_calls=[],
            stop_reason="end_turn",
            model="stub",
            usage=ChatUsage(input_tokens=1, output_tokens=1),
        ),
    ])
    tools = _ScriptedTools([{"ok": True, "result": '"ok"', "error": None}])

    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=tools,
        max_iterations=10,
        # NO activity_store kwarg — must not break.
    )

    _ = [ev async for ev in loop.run(
        [{"role": "user", "content": "do it"}],
        session_id="sNone",
    )]

    assert len(tools.calls) == 1
