"""P6 Phase 6 — AgentLoop integration with TerminationGate (always on).

See openspec/changes/p6-agent-loop-refactor/design.md §"AgentLoop 重构后接口".

Tests cover:
  * 3.1 constructor wiring (gate kwarg + auto-built default)
  * 3.2 gate.allows_call gating LLM calls + record_turn after each call
  * 3.3 gate.allows_tool gating tool dispatch (HARD break on budget — the
    "not convergent" fix vs the old soft-message approach)
  * 3.4 record_final_answer on end_turn, record_error on all_providers_failed

Phase 6: legacy ``_gate is None`` path is gone — every code path goes
through the gate. The auto-built default behaviour replaces the
flag-gated old path.
"""
from __future__ import annotations

from typing import Any

import pytest

from agent.agent_loop import (
    AgentLoop,
    ErrorEvent,
    FinalEvent,
)
from agent.termination import GateConfig, TerminationGate, TerminationReason
from llm.errors import LLMProviderError
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
            raise AssertionError(
                f"ScriptedLLM exhausted after {self.call_count} calls — "
                "test expected loop to stop sooner"
            )
        return self._responses.pop(0)


class _LoopingLLM:
    """Returns a fresh response per call so the loop can run indefinitely
    until something else breaks it.

    Used by 3.8 to verify the gate HARD-breaks the loop (vs old soft cap
    that let it run to max_iterations).
    """

    def __init__(self, response_factory) -> None:
        self._factory = response_factory
        self.call_count = 0

    async def chat_with_fallback(
        self,
        messages: list[dict[str, Any]],
        **_: Any,
    ) -> ChatResponse:
        self.call_count += 1
        return self._factory(self.call_count)


class _RaisingProvider:
    """Provider stub that always raises LLMProviderError — used to test
    chain-mode all_providers_failed path."""

    def __init__(self, provider_id: str = "p1") -> None:
        self.id = provider_id
        self.calls = 0

    async def chat_with_tools(self, *_: Any, **__: Any) -> dict[str, Any]:
        self.calls += 1
        raise LLMProviderError(f"forced failure from {self.id}")


class _ScriptedTools:
    """Replays a fixed sequence of envelope dicts; counts dispatches."""

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
            # Re-use last envelope so the loop can keep going (LoopingLLM)
            return {"ok": True, "result": "ok", "error": None}
        return self._envelopes.pop(0)


def _make_end_turn() -> ChatResponse:
    return ChatResponse(
        content="all done",
        tool_calls=[],
        stop_reason="end_turn",
        usage=ChatUsage(),
        model="stub",
    )


def _make_tool_use(name: str = "noop", tool_id: str = "call_1") -> ChatResponse:
    return ChatResponse(
        content="",
        tool_calls=[ToolCall(id=tool_id, name=name, arguments={})],
        stop_reason="tool_use",
        usage=ChatUsage(),
        model="stub",
    )


# ─────────────── 3.1 constructor wiring ───────────────


class TestAgentLoopConstructorGate:
    def test_agentloop_accepts_termination_gate_kwarg(self):
        """AgentLoop(..., termination_gate=gate) constructs without exception."""
        gate = TerminationGate(GateConfig(max_turns=5))
        loop = AgentLoop(
            llm_registry=_ScriptedLLM([]),
            tool_registry=_ScriptedTools([]),
            termination_gate=gate,
        )
        assert loop._gate is gate

    def test_agentloop_creates_default_gate_when_none_provided(self):
        """P6 Phase 6 — no explicit gate kwarg → AgentLoop always builds
        a default gate. The legacy ``_gate is None`` state is gone."""
        loop = AgentLoop(
            llm_registry=_ScriptedLLM([]),
            tool_registry=_ScriptedTools([]),
        )
        assert loop._gate is not None
        assert isinstance(loop._gate, TerminationGate)

    def test_agentloop_default_gate_has_correct_config(self):
        """Auto-built gate uses max_turns = self.max_iterations."""
        loop = AgentLoop(
            llm_registry=_ScriptedLLM([]),
            tool_registry=_ScriptedTools([]),
            max_iterations=17,
        )
        assert loop._gate is not None
        assert isinstance(loop._gate, TerminationGate)
        assert loop._gate.config.max_turns == 17


# ─────────────── 3.2 gate.allows_call + record_turn ───────────────


class TestAgentLoopGateAllowsCall:
    @pytest.mark.asyncio
    async def test_run_yields_max_turns_error_when_gate_blocks(self):
        """Gate already exhausted at run() start → first event must be
        ErrorEvent(error_max_turns) and loop returns immediately."""
        gate = TerminationGate(GateConfig(max_turns=1))
        # Pre-burn the budget so allows_call returns False.
        gate.state.turns_used = 5
        llm = _ScriptedLLM([_make_end_turn()])  # would succeed if called
        loop = AgentLoop(
            llm_registry=llm,
            tool_registry=_ScriptedTools([]),
            termination_gate=gate,
        )
        events = []
        async for ev in loop.run(messages=[{"role": "user", "content": "hi"}]):
            events.append(ev)
        assert len(events) == 1
        assert isinstance(events[0], ErrorEvent)
        assert events[0].reason == TerminationReason.HARD_MAX_TURNS.value
        # LLM should not have been called
        assert llm.call_count == 0

    @pytest.mark.asyncio
    async def test_run_records_turn_after_llm_call(self):
        """Each LLM call must increment gate.state.turns_used."""
        gate = TerminationGate(GateConfig(max_turns=10))
        llm = _ScriptedLLM([_make_end_turn()])
        loop = AgentLoop(
            llm_registry=llm,
            tool_registry=_ScriptedTools([]),
            termination_gate=gate,
        )
        events = []
        async for ev in loop.run(messages=[{"role": "user", "content": "hi"}]):
            events.append(ev)
        assert gate.state.turns_used >= 1
        assert any(isinstance(ev, FinalEvent) for ev in events)


# ─────────────── 3.3 gate.allows_tool + record_tool_call ───────────────


class TestAgentLoopGateAllowsTool:
    @pytest.mark.asyncio
    async def test_run_blocks_tool_when_budget_exhausted(self):
        """Inject gate with tool_budget_hard=2; LLM emits 3 tool_calls in
        one response. The 3rd dispatch must yield ErrorEvent(error_tool_budget)
        and run() returns early — tools 1 and 2 dispatched normally."""
        gate = TerminationGate(GateConfig(tool_budget_hard=2, max_turns=10))
        response = ChatResponse(
            content="",
            tool_calls=[
                ToolCall(id="c1", name="a", arguments={}),
                ToolCall(id="c2", name="b", arguments={}),
                ToolCall(id="c3", name="c", arguments={}),
            ],
            stop_reason="tool_use",
            usage=ChatUsage(),
            model="stub",
        )
        llm = _ScriptedLLM([response])
        tools = _ScriptedTools([
            {"ok": True, "result": "1", "error": None},
            {"ok": True, "result": "2", "error": None},
        ])
        loop = AgentLoop(
            llm_registry=llm,
            tool_registry=tools,
            termination_gate=gate,
        )
        events = []
        async for ev in loop.run(messages=[{"role": "user", "content": "hi"}]):
            events.append(ev)
        # First two tools dispatched OK
        assert len(tools.calls) == 2
        # Final event is ErrorEvent w/ error_tool_budget
        errors = [ev for ev in events if isinstance(ev, ErrorEvent)]
        assert len(errors) >= 1
        assert errors[-1].reason == TerminationReason.HARD_TOOL_BUDGET.value

    @pytest.mark.asyncio
    async def test_run_hard_breaks_on_tool_budget_unlike_old_soft_msg(self):
        """CRITICAL — gate.tool_budget_hard=3 + LLM emits stop_reason=tool_use
        forever, one tool_call per response. After 3 dispatches, on the
        4th call attempt, allows_tool returns False — loop yields
        ErrorEvent + returns. Main-loop iterations <= 4 (vs old soft cap
        that let it run to max_iterations=20)."""
        gate = TerminationGate(GateConfig(tool_budget_hard=3, max_turns=50))

        def factory(n: int) -> ChatResponse:
            return ChatResponse(
                content="",
                tool_calls=[ToolCall(id=f"call_{n}", name="t", arguments={})],
                stop_reason="tool_use",
                usage=ChatUsage(),
                model="stub",
            )

        llm = _LoopingLLM(factory)
        tools = _ScriptedTools([])
        loop = AgentLoop(
            llm_registry=llm,
            tool_registry=tools,
            termination_gate=gate,
            max_iterations=20,
        )
        events = []
        async for ev in loop.run(messages=[{"role": "user", "content": "hi"}]):
            events.append(ev)
        # 3 successful tool dispatches, then HARD break
        assert len(tools.calls) == 3
        # LLM called at most 4 times (3 tool-use iterations + the 4th
        # one where allows_tool blocks before dispatch).
        assert llm.call_count <= 4
        errors = [ev for ev in events if isinstance(ev, ErrorEvent)]
        assert errors[-1].reason == TerminationReason.HARD_TOOL_BUDGET.value

    @pytest.mark.asyncio
    async def test_run_records_tool_call_per_dispatch(self):
        """After 3 tool dispatches, gate.state.tools_used == 3."""
        gate = TerminationGate(GateConfig(tool_budget_hard=10, max_turns=10))
        response = ChatResponse(
            content="",
            tool_calls=[
                ToolCall(id="c1", name="x", arguments={}),
                ToolCall(id="c2", name="y", arguments={}),
                ToolCall(id="c3", name="z", arguments={}),
            ],
            stop_reason="tool_use",
            usage=ChatUsage(),
            model="stub",
        )
        llm = _ScriptedLLM([response, _make_end_turn()])
        tools = _ScriptedTools([
            {"ok": True, "result": "1", "error": None},
            {"ok": True, "result": "2", "error": None},
            {"ok": True, "result": "3", "error": None},
        ])
        loop = AgentLoop(
            llm_registry=llm,
            tool_registry=tools,
            termination_gate=gate,
        )
        async for _ in loop.run(messages=[{"role": "user", "content": "hi"}]):
            pass
        assert gate.state.tools_used == 3

    @pytest.mark.asyncio
    async def test_run_per_tool_consecutive_break(self):
        """LLM calls 'write_file' 5 times in a row; per_tool_max_consecutive=5
        (default). On the 6th attempt, allows_tool returns
        (False, HALLUCINATION_DETECTED) — loop yields ErrorEvent(hallucination)
        and returns."""
        gate = TerminationGate(GateConfig(
            tool_budget_hard=100,
            max_turns=50,
            per_tool_max_consecutive=5,
        ))

        def factory(n: int) -> ChatResponse:
            return ChatResponse(
                content="",
                tool_calls=[ToolCall(id=f"c{n}", name="write_file", arguments={"i": n})],
                stop_reason="tool_use",
                usage=ChatUsage(),
                model="stub",
            )

        llm = _LoopingLLM(factory)
        tools = _ScriptedTools([])
        loop = AgentLoop(
            llm_registry=llm,
            tool_registry=tools,
            termination_gate=gate,
            max_iterations=20,
        )
        events = []
        async for ev in loop.run(messages=[{"role": "user", "content": "hi"}]):
            events.append(ev)
        # 5 write_file dispatches succeeded, then HALLUCINATION_DETECTED.
        assert len(tools.calls) == 5
        errors = [ev for ev in events if isinstance(ev, ErrorEvent)]
        assert errors[-1].reason == TerminationReason.HALLUCINATION_DETECTED.value


# ─────────────── 3.4 record_final_answer + record_error ───────────────


class TestAgentLoopGateRecordTerminal:
    @pytest.mark.asyncio
    async def test_run_terminates_on_stop_reason_end_turn(self):
        """LLM first response is end_turn → gate.record_final_answer() →
        gate.state.terminated is True with reason SUCCESS."""
        gate = TerminationGate(GateConfig(max_turns=10))
        llm = _ScriptedLLM([_make_end_turn()])
        loop = AgentLoop(
            llm_registry=llm,
            tool_registry=_ScriptedTools([]),
            termination_gate=gate,
        )
        events = []
        async for ev in loop.run(messages=[{"role": "user", "content": "hi"}]):
            events.append(ev)
        assert gate.state.terminated is True
        assert gate.state.terminated_reason == TerminationReason.SUCCESS
        assert any(isinstance(ev, FinalEvent) for ev in events)

    @pytest.mark.asyncio
    async def test_run_records_error_on_all_providers_failed(self):
        """Chain mode, every provider raises LLMProviderError → gate
        records ALL_PROVIDERS_FAILED."""
        gate = TerminationGate(GateConfig(max_turns=10))
        # LLM registry must exist for AgentLoop but isn't called in chain mode
        llm = _ScriptedLLM([])
        tools = _ScriptedTools([])
        loop = AgentLoop(
            llm_registry=llm,
            tool_registry=tools,
            termination_gate=gate,
        )
        chain = [_RaisingProvider("p1"), _RaisingProvider("p2")]
        events = []
        async for ev in loop.run(
            messages=[{"role": "user", "content": "hi"}],
            provider_chain=chain,
        ):
            events.append(ev)
        errors = [ev for ev in events if isinstance(ev, ErrorEvent)]
        assert errors[-1].reason == "all_providers_failed"
        assert gate.state.terminated is True
        assert gate.state.terminated_reason == TerminationReason.ALL_PROVIDERS_FAILED


# P6 Phase 6: legacy `_gate=None` path removed; test deleted accordingly.
# (Was: test_legacy_path_unchanged_when_gate_none.)
