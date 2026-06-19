# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-2.4 — ExternalEvaluator + is_high_consequence_goal + agent_loop wiring.

Tests (TDD, red before green):
  1. is_high_consequence_goal: write tool in ledger → True.
  2. is_high_consequence_goal: 支付 keyword in goal_text → True.
  3. is_high_consequence_goal: ≥5 tool calls → True.
  4. is_high_consequence_goal: reflection confidence < 0.3 → True.
  5. is_high_consequence_goal: pure read-only tools (web_search/read/retrieve) → False.
  6. ExternalEvaluator.evaluate: verdict=revise → returns dict with verdict/issues/quality_score/reason.
  7. ExternalEvaluator: provider=None → safe-fail (returns pass) + evaluator_skipped metric.
  8. agent_loop: flag on + high_consequence + evaluator returns revise → emits evaluator_revise.
  9. agent_loop: flag on + non-high-consequence goal → evaluator NOT called (llm_call count==0).
  10. agent_loop: flag off → evaluator path entirely skipped (BC, evaluator not constructed).
  11. is_auto_resume_trigger("evaluator_revise") is True.
  12. ExternalEvaluator.evaluate: quality_score >= threshold with pass verdict → returns pass (no replan).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, call

import pytest

# ──────────────────────────────────────────────────────────────────────────────
# Helpers shared with other test files
# ──────────────────────────────────────────────────────────────────────────────

from deskpet.tools.receipt import ToolReceipt
from llm.types import ChatResponse, ChatUsage, ToolCall


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
    return ChatUsage(
        input_tokens=10, output_tokens=5, cache_read_tokens=0, cache_write_tokens=0
    )


def _final_resp(content: str) -> ChatResponse:
    return ChatResponse(
        content=content,
        tool_calls=[],
        stop_reason="end_turn",
        usage=_make_usage(),
    )


def _tool_resp(tool_name: str, args: dict) -> ChatResponse:
    return ChatResponse(
        content="",
        tool_calls=[ToolCall(id="tc-1", name=tool_name, arguments=args)],
        stop_reason="tool_use",
        usage=_make_usage(),
    )


class FakeLLMRegistry:
    """Minimal fake LLM registry for agent_loop tests."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def chat_with_fallback(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        model: Optional[str] = None,
        **kwargs,
    ) -> ChatResponse:
        self.calls.append({"messages": messages, "model": model})
        if not self._responses:
            raise RuntimeError("FakeLLMRegistry exhausted")
        resp = self._responses.pop(0)
        if callable(resp):
            return resp(messages)
        return resp


class FakeToolRegistry:
    """Minimal fake tool registry (matches agent_loop expected interface)."""

    def __init__(self, results: Optional[dict] = None) -> None:
        self._results = results or {}
        self.calls: list[dict] = []

    def schemas(self, enabled_toolsets=None) -> list[dict]:
        return []

    def dispatch(self, name: str, args: dict, task_id: str = "") -> Any:
        self.calls.append({"name": name, "args": args})
        return self._results.get(name, json.dumps({"ok": True}))


# ──────────────────────────────────────────────────────────────────────────────
# 1–5. is_high_consequence_goal
# ──────────────────────────────────────────────────────────────────────────────

from deskpet.agent.external_evaluator import is_high_consequence_goal


def test_hc_write_tool_in_ledger():
    """Write tool (ppt_create) in ledger → high consequence."""
    ledger = [_make_receipt("ppt_create")]
    assert is_high_consequence_goal("做一份 PPT", ledger, []) is True


def test_hc_file_write_in_ledger():
    """file_write in ledger → high consequence."""
    ledger = [_make_receipt("file_write")]
    assert is_high_consequence_goal("写文件", ledger, []) is True


def test_hc_keyword_支付_in_goal():
    """支付 keyword in goal_text → high consequence."""
    ledger = []
    assert is_high_consequence_goal("帮我支付账单", ledger, []) is True


def test_hc_keyword_删除_in_goal():
    """删除 keyword in goal_text → high consequence."""
    ledger = []
    assert is_high_consequence_goal("删除这个文件", ledger, []) is True


def test_hc_keyword_发送_in_goal():
    """发送 keyword in goal_text → high consequence."""
    ledger = []
    assert is_high_consequence_goal("发送邮件给老板", ledger, []) is True


def test_hc_five_or_more_tools():
    """≥5 tool calls (ledger entries) → high consequence."""
    ledger = [_make_receipt(f"tool_{i}") for i in range(5)]
    # pure low-risk tools but ≥5 → high consequence
    assert is_high_consequence_goal("搜索一下", ledger, []) is True


def test_hc_four_tools_not_high():
    """4 tool calls, no write/keyword/low-confidence → not high consequence."""
    ledger = [_make_receipt("web_search") for _ in range(4)]
    assert is_high_consequence_goal("搜索一下", ledger, []) is False


def test_hc_low_confidence():
    """reflection confidence < 0.3 → high consequence."""
    ledger = []
    assert is_high_consequence_goal("普通问题", ledger, [], confidence=0.2) is True


def test_hc_confidence_exactly_03_not_triggered():
    """confidence == 0.3 (boundary) → not low-confidence trigger."""
    ledger = []
    assert is_high_consequence_goal("普通问题", ledger, [], confidence=0.3) is False


def test_hc_pure_read_only():
    """Only web_search/read/retrieve → NOT high consequence (cost guard)."""
    ledger = [
        _make_receipt("web_search"),
        _make_receipt("retrieve"),
        _make_receipt("read"),
    ]
    assert is_high_consequence_goal("搜索一下今天天气", ledger, []) is False


def test_hc_empty_ledger_no_keyword():
    """Empty ledger, no keyword, normal confidence → not high consequence."""
    ledger = []
    assert is_high_consequence_goal("你好，今天天气怎么样？", ledger, []) is False


# ──────────────────────────────────────────────────────────────────────────────
# 6. ExternalEvaluator.evaluate — verdict=revise
# ──────────────────────────────────────────────────────────────────────────────

from deskpet.agent.external_evaluator import ExternalEvaluator


@pytest.mark.asyncio
async def test_evaluator_revise_verdict():
    """evaluate returns dict with verdict=revise and quality_score < threshold."""
    revise_json = json.dumps({
        "quality_score": 3,
        "issues": ["产物为空 PPT，缺少内容"],
        "verdict": "revise",
        "reason": "产物不满足目标要求",
    })
    call_count = 0

    async def _fake_llm(prompt: str) -> str:
        nonlocal call_count
        call_count += 1
        return revise_json

    ev = ExternalEvaluator(llm_call=_fake_llm)
    result = await ev.evaluate(
        original_goal="生成一份内容丰富的 PPT",
        produced_artifacts=["report.pptx"],
        objective_evidence=["receipt ok: tool=ppt_create"],
        conversation_summary="调用了 ppt_create，但产出为空",
    )
    assert result["verdict"] == "revise"
    assert result["quality_score"] < 6
    assert len(result["issues"]) > 0
    assert call_count == 1


@pytest.mark.asyncio
async def test_evaluator_pass_verdict():
    """evaluate returns pass when quality is high."""
    pass_json = json.dumps({
        "quality_score": 8,
        "issues": [],
        "verdict": "pass",
        "reason": "产物满足目标要求",
    })

    async def _fake_llm(prompt: str) -> str:
        return pass_json

    ev = ExternalEvaluator(llm_call=_fake_llm)
    result = await ev.evaluate(
        original_goal="生成一份 PPT",
        produced_artifacts=["report.pptx"],
        objective_evidence=["receipt ok: tool=ppt_create"],
        conversation_summary="成功生成",
    )
    assert result["verdict"] == "pass"
    assert result["quality_score"] >= 6


# ──────────────────────────────────────────────────────────────────────────────
# 7. provider=None → safe-fail (returns pass) + evaluator_skipped metric
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_evaluator_provider_none_safe_fail(monkeypatch):
    """provider=None → safe-fail, returns pass verdict, records evaluator_skipped."""
    recorded: list[tuple] = []

    def _fake_record(event_type: str, payload: dict) -> None:
        recorded.append((event_type, payload))

    # Patch metrics_sink.record
    import importlib
    import sys

    # Create a minimal fake metrics_sink if not present
    fake_sink = MagicMock()
    fake_sink.record = _fake_record
    monkeypatch.setitem(sys.modules, "observability.metrics_sink", fake_sink)

    ev = ExternalEvaluator(llm_call=None)
    result = await ev.evaluate(
        original_goal="something",
        produced_artifacts=[],
        objective_evidence=[],
        conversation_summary="",
    )
    assert result["verdict"] == "pass"
    # Check that evaluator_skipped was recorded
    skipped_events = [e for e in recorded if e[0] == "evaluator_skipped"]
    assert len(skipped_events) >= 1


# ──────────────────────────────────────────────────────────────────────────────
# 8. agent_loop: flag on + high_consequence + evaluator returns revise
#    → emits ErrorEvent(reason="evaluator_revise")
# ──────────────────────────────────────────────────────────────────────────────

from agent.agent_loop import AgentLoop, ErrorEvent, FinalEvent


@pytest.mark.asyncio
async def test_agent_loop_evaluator_revise_emits_error_event():
    """Flag on + high-consequence + evaluator revise → ErrorEvent(evaluator_revise)."""
    # The agent will: call ppt_create (write tool) → then try to end_turn.
    # ExternalEvaluator mock returns revise → should emit ErrorEvent.
    llm_responses = [
        _tool_resp("ppt_create", {"title": "报告"}),  # iter 0: tool call
        _final_resp("PPT 已生成"),                      # iter 1: end_turn
    ]
    fake_llm = FakeLLMRegistry(llm_responses)
    fake_tools = FakeToolRegistry({"ppt_create": json.dumps({"ok": True, "path": "/tmp/r.pptx"})})

    # Build a fake receipt_store that returns a ledger with ppt_create receipt
    fake_receipt_store = MagicMock()
    fake_receipt_store.load_session.return_value = [_make_receipt("ppt_create")]

    # Build evaluator that returns revise
    revise_json = json.dumps({
        "quality_score": 2,
        "issues": ["PPT 内容为空"],
        "verdict": "revise",
        "reason": "不满足目标",
    })
    eval_call_count = 0

    async def _revise_llm(prompt: str) -> str:
        nonlocal eval_call_count
        eval_call_count += 1
        return revise_json

    evaluator = ExternalEvaluator(llm_call=_revise_llm)

    agent = AgentLoop(
        llm_registry=fake_llm,
        tool_registry=fake_tools,
        max_iterations=5,
        external_evaluator=evaluator,
        receipt_store=fake_receipt_store,
    )

    events = []
    async for ev in agent.run(
        task_id="t-eval-1",
        session_id="s-eval-1",
        messages=[{"role": "user", "content": "生成一份 PPT"}],
    ):
        events.append(ev)

    error_events = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(error_events) >= 1
    assert any(e.reason == "evaluator_revise" for e in error_events)
    assert eval_call_count == 1  # evaluator called exactly once


# ──────────────────────────────────────────────────────────────────────────────
# 9. agent_loop: flag on + non-high-consequence → evaluator NOT called
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_loop_non_high_consequence_no_evaluator_call():
    """Non-high-consequence goal → evaluator NOT called (llm_call count == 0)."""
    # Only web_search tool, no keywords → NOT high consequence
    llm_responses = [
        _tool_resp("web_search", {"query": "天气"}),
        _final_resp("今天天气晴"),
    ]
    fake_llm = FakeLLMRegistry(llm_responses)
    fake_tools = FakeToolRegistry({"web_search": json.dumps({"results": ["晴"]})})

    fake_receipt_store = MagicMock()
    fake_receipt_store.load_session.return_value = [_make_receipt("web_search")]

    eval_call_count = 0

    async def _eval_llm(prompt: str) -> str:
        nonlocal eval_call_count
        eval_call_count += 1
        return json.dumps({"quality_score": 9, "issues": [], "verdict": "pass", "reason": ""})

    evaluator = ExternalEvaluator(llm_call=_eval_llm)

    agent = AgentLoop(
        llm_registry=fake_llm,
        tool_registry=fake_tools,
        max_iterations=5,
        external_evaluator=evaluator,
        receipt_store=fake_receipt_store,
    )

    events = []
    async for ev in agent.run(
        task_id="t-eval-2",
        session_id="s-eval-2",
        messages=[{"role": "user", "content": "今天天气怎么样？"}],
    ):
        events.append(ev)

    assert eval_call_count == 0, (
        f"Evaluator should NOT be called for non-high-consequence goals, "
        f"but was called {eval_call_count} time(s)"
    )
    # Should still produce a FinalEvent
    final_events = [e for e in events if isinstance(e, FinalEvent)]
    assert len(final_events) >= 1


# ──────────────────────────────────────────────────────────────────────────────
# 10. agent_loop: flag off → evaluator path entirely skipped (BC)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_loop_flag_off_bc():
    """external_evaluator=None (flag off) → evaluator entirely skipped (BC)."""
    llm_responses = [
        _tool_resp("ppt_create", {"title": "报告"}),
        _final_resp("PPT 已生成"),
    ]
    fake_llm = FakeLLMRegistry(llm_responses)
    fake_tools = FakeToolRegistry({"ppt_create": json.dumps({"ok": True})})

    eval_call_count = 0

    async def _eval_llm(prompt: str) -> str:
        nonlocal eval_call_count
        eval_call_count += 1
        return json.dumps({"quality_score": 2, "issues": [], "verdict": "revise", "reason": ""})

    # No external_evaluator passed → None (flag off)
    agent = AgentLoop(
        llm_registry=fake_llm,
        tool_registry=fake_tools,
        max_iterations=5,
        # external_evaluator NOT passed → default None
    )

    events = []
    async for ev in agent.run(
        task_id="t-eval-bc",
        session_id="s-eval-bc",
        messages=[{"role": "user", "content": "生成一份 PPT"}],
    ):
        events.append(ev)

    assert eval_call_count == 0
    final_events = [e for e in events if isinstance(e, FinalEvent)]
    assert len(final_events) >= 1


# ──────────────────────────────────────────────────────────────────────────────
# 11. is_auto_resume_trigger("evaluator_revise") is True
# ──────────────────────────────────────────────────────────────────────────────

from agent.auto_resume import is_auto_resume_trigger


def test_evaluator_revise_in_auto_resume_triggers():
    """evaluator_revise must be in _AUTO_RESUME_TRIGGER_REASONS."""
    assert is_auto_resume_trigger("evaluator_revise") is True


def test_random_reason_not_in_triggers():
    """Unrelated reasons should still return False."""
    assert is_auto_resume_trigger("some_random_reason") is False


# ──────────────────────────────────────────────────────────────────────────────
# 12. Evaluator prompt contract: no persona/emotion leakage
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_evaluator_prompt_no_persona_leakage():
    """Evaluator prompt must NOT include user persona/character fields in INPUT section.

    The spec says: Input WHITELIST contains NO persona/emotion/preference from the
    user's character data (same isolation as WI-2.3). The evaluator's own system
    prompt is allowed to reference these concepts (e.g., to say it doesn't use them),
    but the USER TASK section must not leak character fields like '猫娘' or '性格描述'.
    """
    captured_prompts: list[str] = []

    async def _capture_llm(prompt: str) -> str:
        captured_prompts.append(prompt)
        return json.dumps({
            "quality_score": 8,
            "issues": [],
            "verdict": "pass",
            "reason": "ok",
        })

    ev = ExternalEvaluator(llm_call=_capture_llm)
    # NOTE: we only pass the whitelisted fields (goal, artifacts, evidence, summary).
    # We do NOT pass user_persona, character_description, emotion_state, etc.
    await ev.evaluate(
        original_goal="做一份报告",
        produced_artifacts=["report.docx"],
        objective_evidence=["receipt ok: tool=doc_create"],
        conversation_summary="成功创建",
    )

    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]
    # Must include the goal (whitelist field present)
    assert "做一份报告" in prompt
    # Must NOT include user-character-specific persona fields that would leak
    # from DeskPet's pet persona (these are never passed to evaluate())
    for leaked_field in ("猫娘", "我是你的桌宠", "我叫", "personality_description"):
        assert leaked_field not in prompt, (
            f"Evaluator prompt must not include leaked persona field '{leaked_field}'"
        )
    # The INPUT section (## 原始用户目标) must contain ONLY objective task info
    assert "## 原始用户目标" in prompt
    assert "## 产物清单" in prompt
    assert "## 客观执行证据" in prompt
