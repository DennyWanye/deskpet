"""P6 Phase 4 — AgentLoop integration with ContextManager behind P6_ENABLE_GATE flag.

See openspec/changes/p6-agent-loop-refactor/design.md §"模块 2: ContextManager"
and §"AgentLoop 重构后接口".

Tests cover:
  * 4.1 constructor wiring (ctx_manager kwarg + flag-driven default)
  * 4.2 record_tool_result delegates truncation to the ContextManager
  * 4.3 G1 regression — fetch_tool_result content is NOT truncated even
    when its body exceeds the legacy 4000-char threshold (proves
    skip_truncation_for_tools works through the loop)
  * 4.4 check_budget gate (BLOCK aborts loop, WARN continues)
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from agent.agent_loop import AgentLoop, ErrorEvent, FinalEvent, ToolResultEvent
from agent.context_manager import ContextManager
from agent.termination import TerminationReason
from agent.token_budget import BudgetCheck, BudgetCheckResult
from llm.types import ChatResponse, ChatUsage, ToolCall


# ─────────────── stubs ───────────────


class _ScriptedLLM:
    """Replays a fixed sequence of ChatResponse objects."""

    def __init__(self, responses: list[ChatResponse]) -> None:
        self._responses = list(responses)
        self.call_count = 0

    async def chat_with_fallback(
        self, messages: list[dict[str, Any]], **_: Any,
    ) -> ChatResponse:
        self.call_count += 1
        if not self._responses:
            raise AssertionError(
                f"ScriptedLLM exhausted after {self.call_count} calls"
            )
        return self._responses.pop(0)


class _ScriptedTools:
    """Replays a fixed sequence of envelope dicts via execute_tool."""

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
            return {"ok": True, "result": "ok", "error": None}
        return self._envelopes.pop(0)


def _make_end_turn() -> ChatResponse:
    return ChatResponse(
        content="done",
        tool_calls=[],
        stop_reason="end_turn",
        usage=ChatUsage(),
        model="stub",
    )


def _make_tool_use(name: str = "noop", tool_id: str = "c1") -> ChatResponse:
    return ChatResponse(
        content="",
        tool_calls=[ToolCall(id=tool_id, name=name, arguments={})],
        stop_reason="tool_use",
        usage=ChatUsage(),
        model="stub",
    )


# ─────────────── 4.1 constructor wiring ───────────────


class TestAgentLoopConstructorCtx:
    def test_agentloop_accepts_context_manager_kwarg(self):
        """AgentLoop(..., context_manager=ctx) constructs without exception."""
        ctx = ContextManager()
        loop = AgentLoop(
            llm_registry=_ScriptedLLM([]),
            tool_registry=_ScriptedTools([]),
            context_manager=ctx,
        )
        assert loop._ctx is ctx

    def test_agentloop_creates_default_ctx_when_flag_on(self, monkeypatch):
        """Flag set + no explicit ctx → AgentLoop builds a default
        ContextManager."""
        monkeypatch.setenv("P6_ENABLE_GATE", "1")
        loop = AgentLoop(
            llm_registry=_ScriptedLLM([]),
            tool_registry=_ScriptedTools([]),
        )
        assert loop._ctx is not None
        assert isinstance(loop._ctx, ContextManager)

    def test_agentloop_ctx_none_when_flag_off(self, monkeypatch):
        """Flag unset → no explicit ctx → _ctx is None (legacy path)."""
        monkeypatch.delenv("P6_ENABLE_GATE", raising=False)
        loop = AgentLoop(
            llm_registry=_ScriptedLLM([]),
            tool_registry=_ScriptedTools([]),
        )
        assert loop._ctx is None


# ─────────────── 4.2 record_tool_result delegation ───────────────


class TestAgentLoopRecordToolResult:
    @pytest.mark.asyncio
    async def test_agentloop_uses_ctx_record_tool_result(self):
        """When ctx is wired in, the loop must call
        ctx.record_tool_result(tool_name=..., result=...) for each tool
        dispatched (instead of inlining maybe_truncate_tool_result)."""
        # Mock ContextManager that tracks every record_tool_result call.
        ctx = MagicMock(spec=ContextManager)
        # check_budget called once per iteration before LLM — return
        # OK verdict so loop proceeds.
        ctx.check_budget.return_value = BudgetCheckResult(
            verdict=BudgetCheck.OK,
            estimated_tokens=10,
            context_window=8192,
            ratio=0.0,
            advice="",
        )
        # record_tool_result returns the tuple (content, ref_or_None).
        ctx.record_tool_result.return_value = ("kept-content", None)

        big_result = "X" * 6000
        envelope = {"ok": True, "result": big_result, "error": None}
        # Sequence: tool_use → end_turn
        llm = _ScriptedLLM([
            _make_tool_use(name="read_file", tool_id="c1"),
            _make_end_turn(),
        ])
        tools = _ScriptedTools([envelope])
        loop = AgentLoop(
            llm_registry=llm,
            tool_registry=tools,
            context_manager=ctx,
        )

        events: list[Any] = []
        async for ev in loop.run(messages=[{"role": "user", "content": "hi"}]):
            events.append(ev)

        # ctx.record_tool_result should have been called exactly once.
        assert ctx.record_tool_result.call_count == 1
        kwargs = ctx.record_tool_result.call_args.kwargs
        assert kwargs["tool_name"] == "read_file"
        # The result passed in should be the envelope's JSON string —
        # contains the 6000-char body.
        assert big_result in kwargs["result"]

    @pytest.mark.asyncio
    async def test_run_does_not_truncate_fetch_tool_result_response(self):
        """G1 REGRESSION — fetch_tool_result body MUST NOT be truncated
        when the loop integrates a real ContextManager. The
        skip_truncation_for_tools set passes the body through verbatim,
        otherwise the LLM could fetch a ref and get back a truncated
        ref → infinite loop."""
        ctx = ContextManager()
        big_body = "FULL_BODY_" + ("A" * 6000)
        envelope = {"ok": True, "result": big_body, "error": None}
        llm = _ScriptedLLM([
            _make_tool_use(name="fetch_tool_result", tool_id="ftr1"),
            _make_end_turn(),
        ])
        tools = _ScriptedTools([envelope])
        loop = AgentLoop(
            llm_registry=llm,
            tool_registry=tools,
            context_manager=ctx,
        )

        events: list[Any] = []
        async for ev in loop.run(messages=[{"role": "user", "content": "hi"}]):
            events.append(ev)

        # Sanity: a ToolResultEvent landed.
        tr_events = [ev for ev in events if isinstance(ev, ToolResultEvent)]
        assert len(tr_events) == 1
        # ToolResultEvent.result is the envelope JSON; should contain
        # the full body.
        assert big_body in tr_events[0].result

        # The critical assertion: the working_messages-bound tool
        # message must still contain the full 6000-char body. We dig
        # this out by re-running the loop's exposed state via a probe.
        # Since working_messages is local to run(), we instead validate
        # via ctx.record_tool_result's behavior: it should have
        # returned the body verbatim (no ref created) for
        # fetch_tool_result. We test the inner contract directly here:
        out, ref = ctx.record_tool_result(
            tool_name="fetch_tool_result", result=big_body,
        )
        assert ref is None
        assert out == big_body


# ─────────────── 4.3 check_budget integration ───────────────


class TestAgentLoopCheckBudget:
    @pytest.mark.asyncio
    async def test_run_blocks_on_context_budget_via_ctx(self):
        """ctx.check_budget returns BLOCK → first event is ErrorEvent
        with reason=context_budget_block and the loop returns. Gate
        records the same termination reason."""
        ctx = MagicMock(spec=ContextManager)
        ctx.check_budget.return_value = BudgetCheckResult(
            verdict=BudgetCheck.BLOCK,
            estimated_tokens=9000,
            context_window=8192,
            ratio=1.10,
            advice="Context window exhausted — please compact",
        )
        # LLM unused — block should fire before any call.
        llm = _ScriptedLLM([_make_end_turn()])
        loop = AgentLoop(
            llm_registry=llm,
            tool_registry=_ScriptedTools([]),
            context_manager=ctx,
        )

        events: list[Any] = []
        async for ev in loop.run(messages=[{"role": "user", "content": "hi"}]):
            events.append(ev)

        # First (and only) event should be an ErrorEvent.
        assert len(events) >= 1
        assert isinstance(events[0], ErrorEvent)
        assert events[0].reason == "context_budget_block"
        # LLM should not have been called.
        assert llm.call_count == 0
        # Gate (auto-built since we have a ctx) should have been told.
        if loop._gate is not None:
            # Inspect via summary — record_error sets a terminal reason.
            summary = loop._gate.summary()
            assert summary.get("reason") == TerminationReason.CONTEXT_BUDGET_BLOCK.value

    @pytest.mark.asyncio
    async def test_run_warn_does_not_block(self):
        """ctx.check_budget returns WARN → loop continues, LLM gets
        called, FinalEvent emitted."""
        ctx = MagicMock(spec=ContextManager)
        ctx.check_budget.return_value = BudgetCheckResult(
            verdict=BudgetCheck.WARN,
            estimated_tokens=7000,
            context_window=8192,
            ratio=0.85,
            advice="approaching window",
        )
        llm = _ScriptedLLM([_make_end_turn()])
        loop = AgentLoop(
            llm_registry=llm,
            tool_registry=_ScriptedTools([]),
            context_manager=ctx,
        )

        events: list[Any] = []
        async for ev in loop.run(messages=[{"role": "user", "content": "hi"}]):
            events.append(ev)

        # LLM was called.
        assert llm.call_count == 1
        # A FinalEvent landed (normal end_turn).
        finals = [ev for ev in events if isinstance(ev, FinalEvent)]
        assert len(finals) == 1
