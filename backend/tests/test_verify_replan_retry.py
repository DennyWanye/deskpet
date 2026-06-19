# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-2.2 — verify 失败 → 真重规划重试（TDD 先红后绿）。

测试组覆盖：
  1. 伪完成 → 1st rebound 含反思指令；mock 2nd-round LLM 真调工具 → verify pass。
  2. task_replanning 高相似度(>0.85) → 升级 ephemeral，不消耗 2nd nudge。
  3. ephemeral callable=None → 2nd fail 保守 fail（BC）。
  4. ephemeral callable 返 pass → 救援放行，log ephemeral_rescued。
  5. 三层全失败 → emit verify_exhausted + is_auto_resume_trigger("verify_exhausted")=True。
  6. §7 死循环上限：永久失败任务 total LLM calls <= 显式上限，最终 graceful 退降。

所有测试使用 FakeLLMRegistry（来自 test_deskpet_agent_loop 的模式）+ 真实 AgentLoop。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.agent_loop import AgentLoop, ErrorEvent, FinalEvent
from llm.types import ChatResponse, ChatUsage, ToolCall
from deskpet.agent.verify_gate import (
    Claim,
    ClaimPattern,
    RegexExtractor,
    UnmatchedClaim,
    VerifyGate,
    VerifyOutcome,
    make_ephemeral_verifier,
)
from deskpet.tools.receipt import ToolReceipt
from agent.auto_resume import is_auto_resume_trigger


# ─── helpers ────────────────────────────────────────────────────────────────

def _make_receipt(tool_name: str, ok: bool = True) -> ToolReceipt:
    return ToolReceipt(
        receipt_id="r-" + tool_name,
        tool_name=tool_name,
        args_hash="aabbccdd",
        started_at="2026-06-05T00:00:00Z",
        ended_at="2026-06-05T00:00:01Z",
        duration_ms=1000,
        ok=ok,
    )


def _make_usage() -> ChatUsage:
    return ChatUsage(input_tokens=10, output_tokens=5, cache_read_tokens=0, cache_write_tokens=0)


def _final_resp(content: str) -> ChatResponse:
    """LLM final end_turn response with given assistant text."""
    return ChatResponse(
        content=content,
        tool_calls=[],
        stop_reason="end_turn",
        usage=_make_usage(),
    )


def _tool_resp(tool_name: str, args: dict) -> ChatResponse:
    """LLM response that calls a tool."""
    return ChatResponse(
        content="",
        tool_calls=[ToolCall(id="tc-1", name=tool_name, arguments=args)],
        stop_reason="tool_use",
        usage=_make_usage(),
    )


class FakeLLMRegistry:
    """Minimal sync-queue fake LLM registry."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def chat_with_fallback(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.calls.append({"messages": list(messages), "tools": tools})
        if not self._responses:
            raise AssertionError("FakeLLMRegistry exhausted")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeToolRegistry:
    def __init__(self, handlers: Optional[dict] = None) -> None:
        self._handlers = handlers or {}
        self.calls: list[dict] = []

    def schemas(self, enabled_toolsets=None) -> list[dict]:
        return []

    def dispatch(self, name: str, args: dict, task_id: str) -> Any:
        self.calls.append({"name": name, "args": args})
        if name not in self._handlers:
            raise KeyError(f"tool {name!r} not registered")
        return self._handlers[name](args)


class FakeReceiptStore:
    """Minimal receipt store that can be populated per-round."""

    def __init__(self, ledger: list[ToolReceipt] | None = None) -> None:
        self._ledger: list[ToolReceipt] = list(ledger or [])

    def load_session(self, session_id: str) -> list[ToolReceipt]:
        return list(self._ledger)

    def add(self, receipt: ToolReceipt) -> None:
        self._ledger.append(receipt)


def _claim_pat() -> ClaimPattern:
    return ClaimPattern(
        id="ppt_gen",
        regex=r"已生成.*PPT",
        artifact_kind="pptx",
        tool_hint=["ppt_create"],
    )


async def _collect(agen) -> list[Any]:
    """Drain an async generator into a list."""
    out = []
    async for ev in agen:
        out.append(ev)
    return out


# ─── Test 1: verify fail → reflection rebound → 2nd LLM真调工具 → pass ──────

@pytest.mark.asyncio
async def test_verify_fail_then_replan_succeeds():
    """1st LLM: claim 但不调工具 → verify fail → reflection nudge注入
    2nd LLM: 真调 ppt_create → receipt 加入 ledger → 3rd LLM: end_turn → verify pass。
    """
    receipt_store = FakeReceiptStore()
    pat = _claim_pat()
    gate = VerifyGate(extractor=RegexExtractor([pat]), mode="strict")

    # Round 1: LLM claims "已生成 PPT" but calls no tool → verify gate blocks
    # Round 2: LLM calls ppt_create tool
    # Round 3: LLM calls ppt_create result processing → end_turn again
    # Round 4: Final end_turn with claim
    tool_registry = FakeToolRegistry(handlers={
        "ppt_create": lambda args: {"path": "/tmp/out.pptx"},
    })

    llm = FakeLLMRegistry([
        # iteration 1: LLM says done but didn't call tool → verify blocks
        _final_resp("我已生成 PPT 文件，请查收。"),
        # iteration 2 (after nudge): LLM calls ppt_create
        _tool_resp("ppt_create", {"title": "test"}),
        # iteration 3: after tool result, LLM says done again
        _final_resp("我已生成 PPT 文件，请查收。"),
    ])

    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=tool_registry,
        max_iterations=10,
        verify_gate=gate,
        receipt_store=receipt_store,
        max_verify_nudges=2,
        structured_reflection=True,
    )

    # Intercept the dispatch to add receipt to store
    orig_dispatch = tool_registry.dispatch

    def dispatch_with_receipt(name, args, task_id):
        result = orig_dispatch(name, args, task_id)
        receipt_store.add(_make_receipt(name))
        return result

    tool_registry.dispatch = dispatch_with_receipt

    events = await _collect(loop.run(
        [{"role": "user", "content": "帮我生成PPT"}],
        session_id="sid-1",
    ))

    # Should eventually reach FinalEvent (not ErrorEvent with verify_exhausted)
    error_events = [e for e in events if isinstance(e, ErrorEvent)]
    final_events = [e for e in events if isinstance(e, FinalEvent)]
    # verify_exhausted should NOT be present since 2nd round passes
    verify_exhausted = [e for e in error_events if e.reason == "verify_exhausted"]
    assert not verify_exhausted, f"Should not exhaust: {error_events}"
    assert final_events, "Should get FinalEvent after successful replan"


@pytest.mark.asyncio
async def test_rebound_contains_reflection_instruction():
    """1st verify fail → the injected system message contains task_replanning marker."""
    from deskpet.agent.reflection import _REFLECTION_INSTRUCTION

    receipt_store = FakeReceiptStore()  # empty — no receipts
    pat = _claim_pat()
    gate = VerifyGate(extractor=RegexExtractor([pat]), mode="strict")

    captured_messages: list[list[dict]] = []

    class CaptureLLM:
        calls = 0

        async def chat_with_fallback(self, messages, tools=None, model=None, **kw):
            self.calls += 1
            captured_messages.append(list(messages))
            if self.calls == 1:
                # First call: LLM claims done without tool
                return _final_resp("我已生成 PPT 文件，请查收。")
            # Second+ call: emit final that has no claim to stop loop
            return _final_resp("好的，任务完成。")

    llm = CaptureLLM()
    tool_registry = FakeToolRegistry()

    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=tool_registry,
        max_iterations=5,
        verify_gate=gate,
        receipt_store=receipt_store,
        max_verify_nudges=2,
        structured_reflection=True,
    )

    events = await _collect(loop.run(
        [{"role": "user", "content": "帮我生成PPT"}],
        session_id="sid-rebound",
    ))

    # After 1st verify fail, the 2nd LLM call messages should contain reflection instruction
    assert len(captured_messages) >= 2, "Should have at least 2 LLM calls"
    second_call_msgs = captured_messages[1]
    all_content = " ".join(
        m.get("content", "") for m in second_call_msgs if isinstance(m.get("content"), str)
    )
    assert "task_replanning" in all_content, (
        f"Rebound must contain 'task_replanning'. Messages: {second_call_msgs}"
    )


# ─── Test 2: difflib stagnation → escalate ephemeral, skip 2nd nudge ─────────

@pytest.mark.asyncio
async def test_stagnant_replanning_escalates_to_ephemeral():
    """task_replanning text >0.85 similarity to previous → jumps to ephemeral,
    verify_nudges_used stays at 1 (no 2nd nudge consumed)."""
    receipt_store = FakeReceiptStore()
    pat = _claim_pat()

    # ephemeral will be called
    ephemeral_calls: list[dict] = []

    async def ephemeral(payload: dict) -> bool:
        ephemeral_calls.append(payload)
        return False  # still fails → eventually verify_exhausted

    gate = VerifyGate(
        extractor=RegexExtractor([pat]),
        mode="strict",
        ephemeral_subagent=ephemeral,
    )

    # LLM produces nearly identical task_replanning in both nudge rounds
    stagnant_reflection = json.dumps({
        "error_analysis": "工具没调",
        "execution_critique": "惯性复述",
        "task_replanning": "调用 ppt_create 工具生成 PPT 文件到 /tmp/out.pptx",
        "next_action": "ppt_create",
        "confidence": 0.5,
    })
    # Both rounds produce the same replanning (ratio > 0.85)

    class StagnantLLM:
        calls = 0

        async def chat_with_fallback(self, messages, tools=None, model=None, **kw):
            self.calls += 1
            return _final_resp(
                f"```json\n{stagnant_reflection}\n```\n我已生成 PPT 文件，请查收。"
            )

    llm = StagnantLLM()
    tool_registry = FakeToolRegistry()

    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=tool_registry,
        max_iterations=10,
        verify_gate=gate,
        receipt_store=receipt_store,
        max_verify_nudges=2,
        structured_reflection=True,
    )

    events = await _collect(loop.run(
        [{"role": "user", "content": "帮我生成PPT"}],
        session_id="sid-stagnant",
    ))

    # Ephemeral should have been called (stagnation detected → escalated)
    assert len(ephemeral_calls) >= 1, "Ephemeral should be called on stagnation"

    # Should emit verify_exhausted (ephemeral returns False)
    error_events = [e for e in events if isinstance(e, ErrorEvent)]
    verify_exhausted = [e for e in error_events if e.reason == "verify_exhausted"]
    assert verify_exhausted, f"Should emit verify_exhausted. Errors: {error_events}"


# ─── Test 3: ephemeral callable=None → 2nd fail conservative fail (BC) ───────

@pytest.mark.asyncio
async def test_ephemeral_none_conservative_fail():
    """ephemeral_subagent=None → after nudges exhausted, loop should fail
    without rescue (conservative BC). Emits verify_exhausted."""
    receipt_store = FakeReceiptStore()
    pat = _claim_pat()
    gate = VerifyGate(
        extractor=RegexExtractor([pat]),
        mode="strict",
        ephemeral_subagent=None,  # No rescue
    )

    class AlwaysClaimLLM:
        async def chat_with_fallback(self, messages, tools=None, model=None, **kw):
            return _final_resp("我已生成 PPT 文件，请查收。")

    llm = AlwaysClaimLLM()
    tool_registry = FakeToolRegistry()

    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=tool_registry,
        max_iterations=10,
        verify_gate=gate,
        receipt_store=receipt_store,
        max_verify_nudges=2,
        structured_reflection=False,
    )

    events = await _collect(loop.run(
        [{"role": "user", "content": "帮我生成PPT"}],
        session_id="sid-no-ephemeral",
    ))

    error_events = [e for e in events if isinstance(e, ErrorEvent)]
    verify_exhausted = [e for e in error_events if e.reason == "verify_exhausted"]
    assert verify_exhausted, (
        f"ephemeral=None should emit verify_exhausted. Errors: {error_events}"
    )


# ─── Test 4: ephemeral returns pass → rescue, log ephemeral_rescued ──────────

@pytest.mark.asyncio
async def test_ephemeral_rescue_pass_logs_rescued(caplog):
    """ephemeral callable returns True → verify passes via rescue,
    loop logs 'ephemeral_rescued'."""
    import logging
    caplog.set_level(logging.INFO)

    receipt_store = FakeReceiptStore()
    pat = _claim_pat()

    async def rescue_ephemeral(payload: dict) -> bool:
        return True  # Always rescues

    gate = VerifyGate(
        extractor=RegexExtractor([pat]),
        mode="strict",
        ephemeral_subagent=rescue_ephemeral,
    )

    # LLM will always claim but not call tool → first nudge then ephemeral rescue
    class AlwaysClaimLLM:
        calls = 0

        async def chat_with_fallback(self, messages, tools=None, model=None, **kw):
            self.calls += 1
            return _final_resp("我已生成 PPT 文件，请查收。")

    llm = AlwaysClaimLLM()
    tool_registry = FakeToolRegistry()

    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=tool_registry,
        max_iterations=10,
        verify_gate=gate,
        receipt_store=receipt_store,
        max_verify_nudges=2,
        structured_reflection=False,
    )

    events = await _collect(loop.run(
        [{"role": "user", "content": "帮我生成PPT"}],
        session_id="sid-rescued",
    ))

    # Should NOT emit verify_exhausted (ephemeral rescued)
    error_events = [e for e in events if isinstance(e, ErrorEvent)]
    verify_exhausted = [e for e in error_events if e.reason == "verify_exhausted"]
    assert not verify_exhausted, (
        f"Ephemeral rescue should prevent verify_exhausted. Errors: {error_events}"
    )

    # Should log ephemeral_rescued
    assert any("ephemeral_rescued" in r.message for r in caplog.records), (
        "Should log 'ephemeral_rescued'"
    )


# ─── Test 5: 三层全失败 → verify_exhausted + auto_resume_trigger ──────────────

@pytest.mark.asyncio
async def test_all_layers_fail_emit_verify_exhausted():
    """ephemeral returns False + nudges exhausted → emit verify_exhausted.
    And is_auto_resume_trigger('verify_exhausted') is True."""
    receipt_store = FakeReceiptStore()
    pat = _claim_pat()

    async def failing_ephemeral(payload: dict) -> bool:
        return False

    gate = VerifyGate(
        extractor=RegexExtractor([pat]),
        mode="strict",
        ephemeral_subagent=failing_ephemeral,
    )

    class AlwaysClaimLLM:
        async def chat_with_fallback(self, messages, tools=None, model=None, **kw):
            return _final_resp("我已生成 PPT 文件，请查收。")

    llm = AlwaysClaimLLM()
    tool_registry = FakeToolRegistry()

    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=tool_registry,
        max_iterations=10,
        verify_gate=gate,
        receipt_store=receipt_store,
        max_verify_nudges=2,
        structured_reflection=False,
    )

    events = await _collect(loop.run(
        [{"role": "user", "content": "帮我生成PPT"}],
        session_id="sid-exhausted",
    ))

    error_events = [e for e in events if isinstance(e, ErrorEvent)]
    verify_exhausted = [e for e in error_events if e.reason == "verify_exhausted"]
    assert verify_exhausted, (
        f"All layers fail → must emit verify_exhausted. Errors: {error_events}"
    )
    # auto_resume should handle verify_exhausted
    assert is_auto_resume_trigger("verify_exhausted"), (
        "verify_exhausted must be in _AUTO_RESUME_TRIGGER_REASONS"
    )


# ─── Test 6: §7 dead-loop upper bound ────────────────────────────────────────

@pytest.mark.asyncio
async def test_dead_loop_upper_bound():
    """Permanently failing task: total LLM calls must be <= explicit bound.
    No infinite loop. Final state is graceful degrade (verify_exhausted or
    max_iterations), never hanging."""
    receipt_store = FakeReceiptStore()
    pat = _claim_pat()

    async def failing_ephemeral(payload: dict) -> bool:
        return False

    gate = VerifyGate(
        extractor=RegexExtractor([pat]),
        mode="strict",
        ephemeral_subagent=failing_ephemeral,
    )

    # max_verify_nudges=2, max_iterations=20
    # Upper bound: verify exhausted after nudges+1 LLM calls per "verify episode"
    # Max total calls: max_iterations (hard cap)
    MAX_ITERATIONS = 20
    llm_call_count = [0]

    class CountingLLM:
        async def chat_with_fallback(self, messages, tools=None, model=None, **kw):
            llm_call_count[0] += 1
            if llm_call_count[0] > MAX_ITERATIONS + 5:
                raise AssertionError(f"LLM called too many times: {llm_call_count[0]}")
            return _final_resp("我已生成 PPT 文件，请查收。")

    tool_registry = FakeToolRegistry()
    loop = AgentLoop(
        llm_registry=CountingLLM(),
        tool_registry=tool_registry,
        max_iterations=MAX_ITERATIONS,
        verify_gate=gate,
        receipt_store=receipt_store,
        max_verify_nudges=2,
        structured_reflection=False,
    )

    events = await _collect(loop.run(
        [{"role": "user", "content": "帮我生成PPT"}],
        session_id="sid-deadloop",
    ))

    # Must terminate gracefully
    error_events = [e for e in events if isinstance(e, ErrorEvent)]
    assert error_events, "Should emit at least one ErrorEvent (graceful degrade)"

    # LLM calls must be bounded (< max_iterations + safety margin)
    assert llm_call_count[0] <= MAX_ITERATIONS + 3, (
        f"Too many LLM calls: {llm_call_count[0]}"
    )

    # At least one terminal event (verify_exhausted or max_iterations)
    terminal_reasons = {e.reason for e in error_events}
    assert terminal_reasons & {"verify_exhausted", "max_iterations"}, (
        f"Must have terminal reason. Got: {terminal_reasons}"
    )


# ─── Test 7: make_ephemeral_verifier unit test ───────────────────────────────

@pytest.mark.asyncio
async def test_make_ephemeral_verifier_returns_pass():
    """make_ephemeral_verifier(llm_call) wraps LLM call that returns pass verdict."""

    async def llm_pass(prompt: str) -> str:
        return '{"verdict": "pass", "reason": "claim is covered by ppt_create tool call"}'

    verifier = make_ephemeral_verifier(llm_pass)
    result = await verifier({
        "ledger_size": 1,
        "failed_claims": [{"raw_text": "已生成 PPT", "reason": "no_receipt"}],
        "assistant_text": "我已生成 PPT 文件",
    })
    assert result is True


@pytest.mark.asyncio
async def test_make_ephemeral_verifier_returns_fail():
    """make_ephemeral_verifier(llm_call) wraps LLM call that returns fail verdict."""

    async def llm_fail(prompt: str) -> str:
        return '{"verdict": "fail", "reason": "no matching tool call found"}'

    verifier = make_ephemeral_verifier(llm_fail)
    result = await verifier({
        "ledger_size": 0,
        "failed_claims": [{"raw_text": "已生成 PPT", "reason": "no_receipt"}],
        "assistant_text": "我已生成 PPT 文件",
    })
    assert result is False


@pytest.mark.asyncio
async def test_make_ephemeral_verifier_llm_error_returns_false():
    """make_ephemeral_verifier: LLM raises → safe-fail returns False."""

    async def llm_error(prompt: str) -> str:
        raise RuntimeError("LLM failed")

    verifier = make_ephemeral_verifier(llm_error)
    result = await verifier({
        "ledger_size": 0,
        "failed_claims": [],
        "assistant_text": "x",
    })
    assert result is False


@pytest.mark.asyncio
async def test_make_ephemeral_verifier_garbled_json_returns_false():
    """make_ephemeral_verifier: garbled JSON output → safe-fail returns False."""

    async def llm_garbled(prompt: str) -> str:
        return "I am not JSON at all"

    verifier = make_ephemeral_verifier(llm_garbled)
    result = await verifier({
        "ledger_size": 1,
        "failed_claims": [],
        "assistant_text": "x",
    })
    assert result is False


# ─── Test 8: auto_resume trigger for verify_exhausted ────────────────────────

def test_auto_resume_trigger_verify_exhausted():
    """verify_exhausted must be in auto_resume trigger set."""
    assert is_auto_resume_trigger("verify_exhausted") is True


def test_auto_resume_trigger_existing_reasons_unchanged():
    """Existing reasons still trigger (BC regression guard)."""
    assert is_auto_resume_trigger("max_iterations") is True
    assert is_auto_resume_trigger("permanent_tool_error") is True
    assert is_auto_resume_trigger("hallucination") is True
    assert is_auto_resume_trigger("circuit_open") is True
    assert is_auto_resume_trigger("random_garbage") is False
