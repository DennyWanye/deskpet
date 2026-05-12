"""P6 Phase 5 — End-to-end integration tests.

These tests prove the P6 refactor actually solves the documented bugs:

  * 5.1  Long task force-breaks before max_iterations — the HARD gate
         stops the loop in O(tool_budget_hard) iterations, not at
         max_iterations=50.
  * 5.2  ErrorEvent.reason carries the precise HARD_TOOL_BUDGET value
         (not a generic error_max_turns) so supervisors can diagnose.
  * 5.3  ContextManager.check_budget is honoured by the AgentLoop end
         to end — BLOCK aborts the iteration with the expected reason.
  * 5.4  G1 REGRESSION — fetch_tool_result bodies survive a full
         agent-loop round trip with no truncation (the original
         infinite-loop root cause).

All four tests construct a REAL ``TerminationGate`` + REAL
``ContextManager`` (no mocks for the units under test) and combine the
mock LLM provider + tool registry from ``backend/tests/fixtures/p6.py``
with the AgentLoop. Each test sets ``P6_ENABLE_GATE=1`` via
``monkeypatch.setenv`` so the flag-driven init path is exercised
even when the test runner default has the flag off.
"""
from __future__ import annotations

from typing import Any

import pytest

from agent.agent_loop import (
    AgentLoop,
    ErrorEvent,
    ToolResultEvent,
)
from agent.context_manager import ContextConfig, ContextManager
from agent.termination import GateConfig, TerminationGate, TerminationReason
from llm.types import ChatResponse, ChatUsage, ToolCall


# ─────────────────────────── stubs ───────────────────────────
#
# The P6 fixtures (``make_mock_llm_provider`` / ``make_mock_tool_registry``)
# are shaped for the *provider* layer (raw dicts before the shim
# converts to ChatResponse). AgentLoop's ``llm_registry`` expects
# ``chat_with_fallback`` returning a ChatResponse — i.e. the
# post-shim shape. To keep these integration tests anchored at the
# correct seam we wrap the fixture-style mock with a thin adapter
# that yields ChatResponses directly, matching the Phase 3+4 test
# patterns.


class _LoopingLLM:
    """Returns a fresh ChatResponse per call from a factory.

    Used by 5.1 / 5.2 so the LLM never "runs out" — the loop must
    terminate because of the gate, not because the script is empty.
    """

    def __init__(self, response_factory):
        self._factory = response_factory
        self.call_count = 0

    async def chat_with_fallback(
        self, messages: list[dict[str, Any]], **_: Any,
    ) -> ChatResponse:
        self.call_count += 1
        return self._factory(self.call_count)


class _ScriptedLLM:
    """Replays a fixed sequence of ChatResponses. Raises if drained
    so a test that loops past its budget fails loudly."""

    def __init__(self, responses: list[ChatResponse]) -> None:
        self._responses = list(responses)
        self.call_count = 0

    async def chat_with_fallback(
        self, messages: list[dict[str, Any]], **_: Any,
    ) -> ChatResponse:
        self.call_count += 1
        if not self._responses:
            raise AssertionError(
                f"ScriptedLLM exhausted after {self.call_count} calls "
                "— test expected loop to stop sooner"
            )
        return self._responses.pop(0)


class _ScriptedTools:
    """Minimal v2 ToolRegistry: ``execute_tool`` returns a no-op
    envelope (or a body the test injects)."""

    def __init__(self, envelope_factory=None) -> None:
        self._factory = envelope_factory or (
            lambda name, params: {"ok": True, "result": "ok", "error": None}
        )
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
        return self._factory(name, params)


def _make_tool_use(name: str, tool_id: str, args: dict[str, Any] | None = None) -> ChatResponse:
    return ChatResponse(
        content="",
        tool_calls=[ToolCall(id=tool_id, name=name, arguments=args or {})],
        stop_reason="tool_use",
        usage=ChatUsage(),
        model="stub",
    )


def _make_end_turn(content: str = "done") -> ChatResponse:
    return ChatResponse(
        content=content,
        tool_calls=[],
        stop_reason="end_turn",
        usage=ChatUsage(),
        model="stub",
    )


# ───────────────── 5.1 long task force-breaks ─────────────────


class TestLongTaskForceBreaks:
    @pytest.mark.asyncio
    async def test_long_task_force_breaks_in_under_3min(self, monkeypatch):
        """Mock LLM keeps emitting tool_use forever. With a REAL
        TerminationGate configured tool_budget_hard=3, the loop must
        terminate in well under max_iterations=50 — proving the HARD
        cap actually breaks the loop instead of just nudging the LLM.

        The "in under 3min" framing comes from the original P6 design
        doc — wall_clock is one of the gate's hard limits. Here we
        use ``tool_budget_hard`` as the proxy (deterministic) and
        observe that iteration count is bounded by it, not by
        max_iterations.
        """
        monkeypatch.setenv("P6_ENABLE_GATE", "1")

        # REAL gate — no mocks. Tight tool budget so the loop must
        # stop fast even though max_iterations is 50.
        gate = TerminationGate(GateConfig(
            max_turns=50,
            tool_budget_hard=3,
            wall_clock_seconds=600.0,
        ))

        def factory(n: int) -> ChatResponse:
            return ChatResponse(
                content="",
                tool_calls=[ToolCall(id=f"c{n}", name="noop", arguments={"i": n})],
                stop_reason="tool_use",
                usage=ChatUsage(),
                model="stub",
            )

        llm = _LoopingLLM(factory)
        tools = _ScriptedTools()
        loop = AgentLoop(
            llm_registry=llm,
            tool_registry=tools,
            max_iterations=50,
            termination_gate=gate,
        )

        events: list[Any] = []
        async for ev in loop.run(messages=[{"role": "user", "content": "long task"}]):
            events.append(ev)

        # PROOF: the loop did NOT run to max_iterations.
        # 3 tool dispatches succeeded, then the gate broke on the 4th.
        assert len(tools.calls) == 3, (
            f"expected exactly 3 tool dispatches before HARD break, "
            f"got {len(tools.calls)}"
        )
        # LLM called at most ``tool_budget_hard + 1`` times (the +1 is
        # the iteration where allows_tool blocks before dispatch).
        # Critically: << max_iterations=50.
        assert llm.call_count <= 4, (
            f"expected loop to break in <= 4 LLM calls, got {llm.call_count}"
        )
        # An ErrorEvent landed
        errors = [ev for ev in events if isinstance(ev, ErrorEvent)]
        assert len(errors) >= 1
        # Iteration count from the last event is bounded.
        assert gate.state.turns_used <= 4
        assert gate.state.tools_used == 3

    @pytest.mark.asyncio
    async def test_long_task_breaks_with_tool_budget_reason(self, monkeypatch):
        """The ErrorEvent.reason must be ``error_tool_budget`` (the
        TerminationReason.HARD_TOOL_BUDGET enum value), NOT a generic
        ``error_max_turns`` or supervisor self-check message.

        This is the proof the hard cap is actually hard — the legacy
        soft-cap path would have surfaced max_turns or just kept
        looping until the iteration counter ran out.
        """
        monkeypatch.setenv("P6_ENABLE_GATE", "1")

        gate = TerminationGate(GateConfig(
            max_turns=50,
            tool_budget_hard=3,
            wall_clock_seconds=600.0,
        ))

        def factory(n: int) -> ChatResponse:
            return ChatResponse(
                content="",
                tool_calls=[ToolCall(id=f"c{n}", name="noop", arguments={})],
                stop_reason="tool_use",
                usage=ChatUsage(),
                model="stub",
            )

        llm = _LoopingLLM(factory)
        tools = _ScriptedTools()
        loop = AgentLoop(
            llm_registry=llm,
            tool_registry=tools,
            max_iterations=50,
            termination_gate=gate,
        )

        events: list[Any] = []
        async for ev in loop.run(messages=[{"role": "user", "content": "hi"}]):
            events.append(ev)

        errors = [ev for ev in events if isinstance(ev, ErrorEvent)]
        assert len(errors) >= 1
        # The LAST error is the termination reason
        assert errors[-1].reason == TerminationReason.HARD_TOOL_BUDGET.value
        # Sanity — value really is "error_tool_budget"
        assert errors[-1].reason == "error_tool_budget"
        # And it's NOT error_max_turns
        assert errors[-1].reason != TerminationReason.HARD_MAX_TURNS.value


# ───────────────── 5.3 history compaction / budget BLOCK ─────────────────


class TestHistoryCompactionBudget:
    @pytest.mark.asyncio
    async def test_history_compaction_keeps_loop_under_budget(self, monkeypatch):
        """Construct a REAL ContextManager with an artificially low
        ``budget_block_pct`` so even a tiny history trips BLOCK. Feed
        25 messages to the loop. Expectation: the budget guard kicks
        BEFORE the LLM is ever called and yields
        ErrorEvent(reason='context_budget_block'). This proves the
        ContextManager.check_budget integration is wired end-to-end.

        (We assert the BLOCK path; the WARN path is covered by Phase 4
        unit tests. Either gives us confidence the wiring is real.)
        """
        monkeypatch.setenv("P6_ENABLE_GATE", "1")

        # Artificially tiny budget threshold so any non-empty history
        # trips BLOCK. block_pct=0.001 → even 25 short messages
        # exceeds the threshold.
        ctx = ContextManager(ContextConfig(
            budget_warn_pct=0.0005,
            budget_block_pct=0.001,
        ))

        # 25 short messages — enough for compaction trigger but
        # tiny in token terms. The point is the budget *ratio* is
        # forced into BLOCK territory by the artificial threshold.
        history = [{"role": "system", "content": "You are a helpful assistant."}]
        for i in range(24):
            role = "user" if i % 2 == 0 else "assistant"
            history.append({"role": role, "content": f"turn-{i} {role} message"})

        # LLM should never be called.
        llm = _ScriptedLLM([_make_end_turn()])
        tools = _ScriptedTools()
        loop = AgentLoop(
            llm_registry=llm,
            tool_registry=tools,
            context_manager=ctx,
            default_model="stub-model",
        )

        events: list[Any] = []
        async for ev in loop.run(messages=history):
            events.append(ev)

        # First event must be ErrorEvent(context_budget_block).
        assert len(events) >= 1
        assert isinstance(events[0], ErrorEvent)
        assert events[0].reason == "context_budget_block"
        # LLM was NOT called — block fired before the call.
        assert llm.call_count == 0


# ───────────────── 5.4 G1 fetch_tool_result round trip ─────────────────


class TestG1FetchToolResultRoundTrip:
    @pytest.mark.asyncio
    async def test_fetch_tool_result_round_trip_no_truncation(self, monkeypatch):
        """G1 REGRESSION — the FULL agent-loop round trip.

        The legacy bug: when LLM called ``fetch_tool_result`` to
        retrieve a previously-truncated body, the returned body
        itself went through ``maybe_truncate_tool_result`` again and
        landed in working_messages as a "[truncated, ref_id=X]"
        marker. Then the next iteration the LLM would fetch that
        ref and the new response would also be truncated… infinite
        loop.

        The P6 fix: ``ContextManager.record_tool_result`` honours
        ``skip_truncation_for_tools = {"fetch_tool_result"}`` so the
        fetch body passes through verbatim. This test proves the
        agent loop wiring honours that contract end-to-end.

        We probe via two channels:

          1. ToolResultEvent — the event yielded for downstream
             persistence/UI must carry the full 6000-char body.
          2. ContextManager.record_tool_result — direct call with
             the same inputs returns ``(full_body, None)`` (no ref).
             This is the contract the agent loop relies on; if the
             loop changed and stopped delegating, this contract
             check would still hold but channel 1 would regress.
        """
        monkeypatch.setenv("P6_ENABLE_GATE", "1")

        # REAL ContextManager — no mocks for the unit under test.
        ctx = ContextManager()

        big_body = "FULL_BODY_" + ("A" * 6000)
        envelope = {"ok": True, "result": big_body, "error": None}

        llm = _ScriptedLLM([
            _make_tool_use(name="fetch_tool_result", tool_id="ftr1",
                           args={"ref_id": "abc123"}),
            _make_end_turn(),
        ])
        tools = _ScriptedTools(envelope_factory=lambda n, p: envelope)
        loop = AgentLoop(
            llm_registry=llm,
            tool_registry=tools,
            context_manager=ctx,
        )

        events: list[Any] = []
        async for ev in loop.run(messages=[{"role": "user", "content": "hi"}]):
            events.append(ev)

        # Channel 1 — ToolResultEvent carries the full body
        tr_events = [ev for ev in events if isinstance(ev, ToolResultEvent)]
        assert len(tr_events) == 1, f"expected 1 ToolResultEvent, got {len(tr_events)}"
        assert big_body in tr_events[0].result, (
            "fetch_tool_result body was truncated in ToolResultEvent — "
            "this is the G1 regression"
        )

        # Channel 2 — direct ContextManager contract: fetch body
        # round-trips verbatim with no ref.
        out, ref = ctx.record_tool_result(
            tool_name="fetch_tool_result", result=big_body,
        )
        assert ref is None, (
            "fetch_tool_result body was given a ref — the "
            "skip_truncation_for_tools fix regressed"
        )
        assert out == big_body, (
            "fetch_tool_result body was modified by ContextManager — "
            "G1 root-cause"
        )

        # Sanity — a regular tool would have been truncated.
        regular_out, regular_ref = ctx.record_tool_result(
            tool_name="read_file", result=big_body,
        )
        # 6000 chars > default threshold (4000) → ref expected
        assert regular_ref is not None
        assert len(regular_out) < len(big_body)
