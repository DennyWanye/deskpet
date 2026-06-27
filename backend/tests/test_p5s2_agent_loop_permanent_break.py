# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P5-S2 Phase 2.2: agent loop must break out on permanent tool error.

The motivating bug (vpn-tunnel, 2026-05-10): the LLM kept invoking
``write_file`` without the required ``path`` argument, the tool returned
``{"ok": false, "error": "missing required parameter: path"}`` every
time, and the loop kept ReAct'ing for 50 iterations until hitting
``max_iterations``. Each iteration cost a full LLM round-trip.

Phase 2 fix: classify every tool result. If ``classify(...)`` returns
:class:`PermanentToolError`, the loop SHALL emit ``ErrorEvent(reason=
"permanent_tool_error")`` and ``return`` immediately — NOT continue to
iteration 2 and beyond.

Tolerance for backward compat:
- The first iteration always runs (we can't predict the LLM is going to
  give us a broken tool_call before it does so).
- Tool dispatch is concurrent within a turn; the loop must yield the
  ``tool_result`` for every concurrent call before breaking.
- ``TransientToolError`` results MUST NOT break — they go back to the
  LLM as normal tool_result messages.
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
from llm.types import ChatResponse, ChatUsage, ToolCall


# ─────────────── stubs ───────────────


class _ScriptedLLM:
    """Replays a fixed sequence of ChatResponse objects in order."""

    def __init__(self, responses: list[ChatResponse]) -> None:
        self._responses = list(responses)
        self.call_count = 0

    async def chat_with_fallback(
        self,
        messages: list[dict[str, Any]],
        **_: Any,
    ) -> ChatResponse:
        self.call_count += 1
        if not self._responses:
            # If the loop kept calling past what we scripted, that's the
            # bug we're guarding against — fail loudly so test diagnoses
            # "loop didn't break, it iterated past max_iterations again".
            raise AssertionError(
                f"ScriptedLLM exhausted after {self.call_count} calls — "
                "loop did not break out on permanent error"
            )
        return self._responses.pop(0)


class _ScriptedTools:
    """Replays a fixed sequence of envelope dicts in order, one per
    ``execute_tool`` call. Counts calls so tests can assert dispatch
    was invoked exactly N times (not 50)."""

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
            # Same diagnostic as ScriptedLLM
            raise AssertionError(
                f"ScriptedTools exhausted after {len(self.calls)} calls — "
                "loop did not break out on permanent tool error"
            )
        return self._envelopes.pop(0)


def _resp_with_tool_call(
    tool_id: str = "call_1",
    tool_name: str = "write_file",
    args: dict | None = None,
) -> ChatResponse:
    """Build a ChatResponse that asks for one tool_call."""
    return ChatResponse(
        content="",
        tool_calls=[ToolCall(id=tool_id, name=tool_name, arguments=args or {})],
        stop_reason="tool_use",
        model="stub",
        usage=ChatUsage(input_tokens=1, output_tokens=1),
    )


def _envelope_missing_param() -> dict[str, Any]:
    """The exact envelope shape registry.execute_tool returns when a
    handler raised ValueError("missing required parameter: path") or
    similar."""
    return {
        "ok": False,
        "result": None,
        "error": "missing required parameter: path",
    }


def _envelope_timeout() -> dict[str, Any]:
    """Transient — the loop should NOT break on this."""
    return {
        "ok": False,
        "result": None,
        "error": "timeout",
    }


def _envelope_tool_not_found() -> dict[str, Any]:
    return {
        "ok": False,
        "result": None,
        "error": "tool_not_found",
    }


# ─────────────── tests ───────────────


@pytest.mark.asyncio
async def test_permanent_error_breaks_after_first_iteration() -> None:
    """The loop sees ONE permanent error envelope and stops — no second
    LLM call, no 50 iterations of waste."""
    llm = _ScriptedLLM([
        _resp_with_tool_call(),
        # Nothing else scripted — if the loop keeps calling, ScriptedLLM
        # raises AssertionError.
    ])
    tools = _ScriptedTools([_envelope_missing_param()])

    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=tools,
        max_iterations=50,  # high cap proves we stopped early
    )

    events = [
        ev async for ev in loop.run(
            [{"role": "user", "content": "create test.txt with hello"}],
            session_id="sX",
        )
    ]

    # Exactly one LLM call (no second iteration).
    assert llm.call_count == 1, f"loop did not break: {llm.call_count} calls"
    # Exactly one tool dispatch.
    assert len(tools.calls) == 1

    # We must have emitted an ErrorEvent with reason permanent_tool_error.
    err_events = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(err_events) == 1, f"expected 1 ErrorEvent, got: {events!r}"
    assert err_events[0].reason == "permanent_tool_error"
    assert "missing required parameter" in err_events[0].detail.lower()

    # Specifically NOT a max_iterations or final event.
    assert not any(isinstance(e, FinalEvent) for e in events)
    assert not any(
        isinstance(e, ErrorEvent) and e.reason == "max_iterations" for e in events
    )

    # The tool_result event for the failing call MUST still be yielded
    # (callers persist it; supervisor needs it for diagnosis).
    assert any(isinstance(e, ToolResultEvent) for e in events)


@pytest.mark.asyncio
async def test_permanent_error_does_not_iterate_50_times() -> None:
    """The original bug: even though max_iterations=50, the loop should
    stop after iteration 1 when a permanent error arrives. This proves
    we don't degrade silently."""
    llm = _ScriptedLLM(
        # Pre-script enough turns to absolutely prove we DON'T need them.
        [_resp_with_tool_call(tool_id=f"c{i}") for i in range(50)]
    )
    tools = _ScriptedTools([_envelope_missing_param()] * 50)

    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=tools,
        max_iterations=50,
    )

    events = [
        ev async for ev in loop.run(
            [{"role": "user", "content": "go"}],
            session_id="sBug",
        )
    ]

    # The whole point: ≤3 iterations, not 50 (spec 2.11 acceptance bar).
    assert llm.call_count <= 3, (
        f"loop iterated {llm.call_count} times — permanent break-out failed"
    )
    assert len(tools.calls) <= 3

    err_events = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(err_events) == 1
    assert err_events[0].reason == "permanent_tool_error"
    assert err_events[0].iteration <= 3


@pytest.mark.asyncio
async def test_transient_error_does_NOT_break_loop() -> None:
    """Regression guard: TransientToolError must continue the ReAct
    loop — only Permanent breaks. This test would catch a too-eager
    classifier that breaks on every error envelope."""
    llm = _ScriptedLLM([
        _resp_with_tool_call(tool_id="c1"),  # turn 1: tool_use
        # turn 2: LLM saw the timeout envelope, decides to give up cleanly.
        ChatResponse(
            content="The tool timed out, sorry.",
            tool_calls=[],
            stop_reason="end_turn",
            model="stub",
            usage=ChatUsage(input_tokens=1, output_tokens=1),
        ),
    ])
    tools = _ScriptedTools([_envelope_timeout()])

    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=tools,
        max_iterations=10,
    )

    events = [
        ev async for ev in loop.run(
            [{"role": "user", "content": "do thing"}],
            session_id="sTrans",
        )
    ]

    # Loop continued normally → 2 LLM calls.
    assert llm.call_count == 2
    # FinalEvent emitted, NOT ErrorEvent.
    assert any(isinstance(e, FinalEvent) for e in events)
    err_events = [e for e in events if isinstance(e, ErrorEvent)]
    assert err_events == [], f"transient should not produce ErrorEvent: {err_events!r}"


@pytest.mark.asyncio
async def test_hallucination_breaks_loop_with_distinct_reason() -> None:
    """`tool_not_found` → loop breaks with reason='hallucination', not
    'permanent_tool_error'. Distinct reason lets supervisor route
    differently (Phase 4)."""
    llm = _ScriptedLLM([
        _resp_with_tool_call(tool_name="do_magic"),  # tool that doesn't exist
    ])
    tools = _ScriptedTools([_envelope_tool_not_found()])

    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=tools,
        max_iterations=10,
    )

    events = [
        ev async for ev in loop.run(
            [{"role": "user", "content": "use do_magic"}],
            session_id="sHallu",
        )
    ]

    assert llm.call_count == 1  # broke after first iteration
    err_events = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(err_events) == 1
    assert err_events[0].reason == "hallucination"


@pytest.mark.asyncio
async def test_first_call_succeeds_then_permanent_breaks() -> None:
    """Mixed timeline: tool succeeds, LLM does another round, second
    tool returns permanent error → loop must still break at iteration 2.
    Proves the check runs every iteration, not just the first."""
    # Turn 1: LLM calls tool (succeeds), loop iterates.
    # Turn 2: LLM calls tool again (permanent error), loop breaks.
    llm = _ScriptedLLM([
        _resp_with_tool_call(tool_id="c1", tool_name="read_file"),
        _resp_with_tool_call(tool_id="c2", tool_name="write_file"),
    ])
    tools = _ScriptedTools([
        {"ok": True, "result": '"file contents"', "error": None},
        _envelope_missing_param(),
    ])

    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=tools,
        max_iterations=10,
    )

    events = [
        ev async for ev in loop.run(
            [{"role": "user", "content": "go"}],
            session_id="sMix",
        )
    ]

    assert llm.call_count == 2
    assert len(tools.calls) == 2
    err_events = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(err_events) == 1
    assert err_events[0].reason == "permanent_tool_error"
    assert err_events[0].iteration == 2


@pytest.mark.asyncio
async def test_permanent_error_in_one_of_concurrent_calls_breaks() -> None:
    """When a turn contains multiple tool_calls and ONE returns
    permanent error: the loop yields all tool_results from that turn,
    then breaks. We don't want to lose the other tool's result event,
    but we also don't want to launch turn 2."""
    llm = _ScriptedLLM([
        ChatResponse(
            content="",
            tool_calls=[
                ToolCall(id="c1", name="read_file", arguments={"path": "a.txt"}),
                ToolCall(id="c2", name="write_file", arguments={}),  # missing path
            ],
            stop_reason="tool_use",
            model="stub",
            usage=ChatUsage(input_tokens=1, output_tokens=1),
        ),
    ])
    tools = _ScriptedTools([
        {"ok": True, "result": '"hi"', "error": None},
        _envelope_missing_param(),
    ])

    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=tools,
        max_iterations=10,
    )

    events = [
        ev async for ev in loop.run(
            [{"role": "user", "content": "go"}],
            session_id="sConcur",
        )
    ]

    # Both tool_results yielded.
    tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(tool_results) == 2

    # But loop broke — only 1 LLM call.
    assert llm.call_count == 1
    err_events = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(err_events) == 1
    assert err_events[0].reason == "permanent_tool_error"
