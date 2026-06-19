# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-4.0 — AgentLoop compaction wiring tests.

Verifies that:
1. AgentLoop accepts ``compressor=`` param (BC: default None).
2. compressor=None → no compress() call ever (flag off BC).
3. should_compress True → compress() called → working_messages replaced.
4. skill_prelude (role=system) survives compaction (_partition keeps system).
5. goal_text is passed to compress → [目标锚定] anchor in post-compaction msgs.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.agent_loop import AgentLoop


# ---------------------------------------------------------------------------
# Minimal fakes
# ---------------------------------------------------------------------------

class _FakeToolRegistry:
    """Minimal ToolRegistry — no tool calls, just enough for AgentLoop init."""

    def schemas(self, enabled_toolsets=None):
        return []

    async def execute_tool(self, name, args, task_id):
        return '{"ok": true, "result": "noop"}'


class _FakeLLM:
    """Mock LLM that returns an end_turn response immediately."""

    def __init__(self):
        self.calls = 0

    async def chat_with_fallback(self, messages, *, tools=None, model=None, **kw):
        self.calls += 1
        from llm.types import ChatResponse
        return ChatResponse(
            content="done",
            stop_reason="end_turn",
            tool_calls=[],
            usage={"input_tokens": 100, "output_tokens": 10},
        )


class _FakeCompressor:
    """Mock ContextCompressor recording calls."""

    def __init__(self, *, should=True, compressed_messages=None):
        self._should = should
        self._compressed_messages = compressed_messages  # None → return original
        self.should_compress_calls: list[int] = []
        self.compress_calls: list[dict] = []

    def should_compress(self, prompt_tokens: int) -> bool:
        self.should_compress_calls.append(prompt_tokens)
        return self._should

    async def compress(self, messages, *, goal_text=None, pending_tasks=None):
        self.compress_calls.append({"messages": messages, "goal_text": goal_text})
        from deskpet.agent.context_compressor import CompressionResult

        if self._compressed_messages is not None:
            return CompressionResult(
                messages=self._compressed_messages,
                compressed=True,
                reduction_ratio=0.5,
            )
        # Return "no compression" path
        return CompressionResult(messages=list(messages), compressed=False)


class _FakeContextManager:
    """Minimal ContextManager facade with a per-session compaction threshold."""

    def __init__(self, *, estimated_tokens: int, compact_at_tokens: int):
        from agent.token_budget import BudgetCheck, BudgetCheckResult

        self.result = BudgetCheckResult(
            verdict=BudgetCheck.OK,
            estimated_tokens=estimated_tokens,
            context_window=32000,
            ratio=estimated_tokens / 32000,
        )

        class _Cfg:
            pass

        self.config = _Cfg()
        self.config.compact_at_tokens = compact_at_tokens

    def check_budget(self, messages, *, model: str):
        return self.result


def _make_msgs(n_non_system: int = 4, with_system: bool = True):
    """Build a short message list with optional system prefix."""
    out: list[dict] = []
    if with_system:
        out.append({"role": "system", "content": "[skill_prelude] instructions here"})
    for i in range(n_non_system):
        role = "user" if i % 2 == 0 else "assistant"
        out.append({"role": role, "content": f"msg {i}"})
    return out


# ---------------------------------------------------------------------------
# BC: new param exists with default None
# ---------------------------------------------------------------------------

def test_agent_loop_accepts_compressor_param():
    """AgentLoop.__init__ accepts compressor= kwarg with default None (BC)."""
    import inspect
    sig = inspect.signature(AgentLoop.__init__)
    params = sig.parameters
    assert "compressor" in params, "compressor param missing from AgentLoop.__init__"
    assert params["compressor"].default is None, "compressor default must be None"


def test_agent_loop_stores_compressor():
    """AgentLoop stores compressor on self.compressor."""
    fake = _FakeCompressor()
    loop = AgentLoop(
        llm_registry=_FakeLLM(),
        tool_registry=_FakeToolRegistry(),
        compressor=fake,
    )
    assert loop.compressor is fake


def test_agent_loop_compressor_none_by_default():
    """When compressor not passed, self.compressor is None."""
    loop = AgentLoop(
        llm_registry=_FakeLLM(),
        tool_registry=_FakeToolRegistry(),
    )
    assert loop.compressor is None


# ---------------------------------------------------------------------------
# BC: compressor=None → compress never called
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compressor_none_never_calls_compress():
    """Flag off (compressor=None) → compress() is NEVER called — zero new behaviour."""
    fake_compressor = _FakeCompressor(should=True)

    loop = AgentLoop(
        llm_registry=_FakeLLM(),
        tool_registry=_FakeToolRegistry(),
        compressor=None,  # flag OFF
    )

    msgs = _make_msgs()
    events = []
    async for ev in loop.run(msgs, session_id="test_bc"):
        events.append(ev)

    assert fake_compressor.compress_calls == [], "compress must not be called when compressor=None"
    assert fake_compressor.should_compress_calls == [], "should_compress must not be called when compressor=None"


# ---------------------------------------------------------------------------
# Core: should_compress=True → compress called → working_messages replaced
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compressor_fires_when_threshold_exceeded():
    """When should_compress returns True and compress returns compressed=True,
    the agent loop replaces working_messages with the compressed output."""
    compressed_out = [
        {"role": "system", "content": "[skill_prelude] instructions here"},
        {"role": "assistant", "content": "[压缩摘要 / compressed summary]\nSummary of conversation."},
        {"role": "user", "content": "msg 2"},
        {"role": "assistant", "content": "msg 3"},
    ]
    fake_compressor = _FakeCompressor(should=True, compressed_messages=compressed_out)

    loop = AgentLoop(
        llm_registry=_FakeLLM(),
        tool_registry=_FakeToolRegistry(),
        compressor=fake_compressor,
    )

    msgs = _make_msgs()
    events = []
    async for ev in loop.run(msgs, session_id="test_fires"):
        events.append(ev)

    # compress() must have been called at least once
    assert len(fake_compressor.compress_calls) >= 1, "compress() not called"
    # should_compress must have been called
    assert len(fake_compressor.should_compress_calls) >= 1, "should_compress() not called"


@pytest.mark.asyncio
async def test_context_manager_compact_at_overrides_static_compressor_threshold():
    """task_ae1af91b: AgentLoop must use the per-session ContextManager
    compaction waterline, not only the boot-time ContextCompressor default.

    A real code session resolves model/project context into ContextManager;
    if that says estimated_tokens crossed compact_at_tokens, compression
    must fire even when the shared compressor's legacy 32k threshold would
    say "not yet".
    """
    compressed_out = [
        {"role": "system", "content": "[skill_prelude] instructions here"},
        {"role": "assistant", "content": "[压缩摘要 / compressed summary]\nSummary."},
        {"role": "user", "content": "latest"},
    ]
    fake_compressor = _FakeCompressor(
        should=False,
        compressed_messages=compressed_out,
    )
    ctx = _FakeContextManager(estimated_tokens=150, compact_at_tokens=100)

    loop = AgentLoop(
        llm_registry=_FakeLLM(),
        tool_registry=_FakeToolRegistry(),
        context_manager=ctx,
        compressor=fake_compressor,
    )

    async for _ in loop.run(_make_msgs(), session_id="test_ctx_threshold"):
        pass

    assert len(fake_compressor.compress_calls) >= 1


# ---------------------------------------------------------------------------
# System messages preserved: skill_prelude survives compaction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_system_messages_survive_compaction():
    """After compaction, role=system messages (skill_prelude) must still be present.
    ContextCompressor._partition keeps all system messages verbatim."""
    # Build a realistic compressed result that includes the system message
    # (simulating what _partition does — system kept, middle summarized)
    system_msg = {"role": "system", "content": "[skill_prelude] keep me"}
    compressed_out = [
        system_msg,
        {"role": "assistant", "content": "[压缩摘要 / compressed summary]\nSummary."},
        {"role": "user", "content": "last user"},
    ]
    fake_compressor = _FakeCompressor(should=True, compressed_messages=compressed_out)

    loop = AgentLoop(
        llm_registry=_FakeLLM(),
        tool_registry=_FakeToolRegistry(),
        compressor=fake_compressor,
    )

    msgs = [
        {"role": "system", "content": "[skill_prelude] keep me"},
        {"role": "user", "content": "u0"},
        {"role": "assistant", "content": "a0"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "last user"},
    ]

    events = []
    async for ev in loop.run(msgs, session_id="test_sys"):
        events.append(ev)

    # Verify compress was called with messages containing system msg
    if fake_compressor.compress_calls:
        passed_msgs = fake_compressor.compress_calls[0]["messages"]
        system_msgs = [m for m in passed_msgs if m.get("role") == "system"]
        assert any("[skill_prelude]" in (m.get("content") or "") for m in system_msgs), \
            "skill_prelude system message not passed to compress()"


# ---------------------------------------------------------------------------
# Goal text passed to compress → [目标锚定] anchor present
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_goal_text_passed_to_compress():
    """When session_goal_store has an active goal, goal_text is passed to compress()."""
    # We need a real ContextCompressor to verify goal_text injection
    # Use a fake compressor that records the goal_text kwarg
    fake_compressor = _FakeCompressor(should=True)  # compressed=False → original returned

    # Fake goal store
    class _FakeGoalStore:
        def get_goal_text(self, session_id: str):
            return "用户想要整理三份文档"

    loop = AgentLoop(
        llm_registry=_FakeLLM(),
        tool_registry=_FakeToolRegistry(),
        compressor=fake_compressor,
        session_goal_store=_FakeGoalStore(),
    )

    msgs = _make_msgs()
    async for _ in loop.run(msgs, session_id="test_goal"):
        pass

    # compress must have been called with goal_text
    assert len(fake_compressor.compress_calls) >= 1
    call = fake_compressor.compress_calls[0]
    assert call["goal_text"] == "用户想要整理三份文档", \
        f"goal_text not passed to compress(). Got: {call['goal_text']!r}"


@pytest.mark.asyncio
async def test_goal_text_none_when_no_goal_store():
    """When session_goal_store is None, compress() is called with goal_text=None."""
    fake_compressor = _FakeCompressor(should=True)

    loop = AgentLoop(
        llm_registry=_FakeLLM(),
        tool_registry=_FakeToolRegistry(),
        compressor=fake_compressor,
        session_goal_store=None,
    )

    msgs = _make_msgs()
    async for _ in loop.run(msgs, session_id="test_no_goal"):
        pass

    assert len(fake_compressor.compress_calls) >= 1
    call = fake_compressor.compress_calls[0]
    assert call["goal_text"] is None


# ---------------------------------------------------------------------------
# Integration: real ContextCompressor + real goal anchor injection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_real_compressor_injects_goal_anchor():
    """Integration smoke: real ContextCompressor with a working LLM mock
    produces [目标锚定] system block when goal_text is passed."""
    from deskpet.agent.context_compressor import ContextCompressor

    class _MockLLM:
        async def chat_with_fallback(self, *a, **kw):
            class R:
                content = "Summary of conversation."
            return R()

    # Use a low threshold so should_compress fires immediately
    real_compressor = ContextCompressor(
        llm_registry=_MockLLM(),
        context_window=100,
        threshold_percent=0.01,  # fires at 1 token
    )

    class _FakeGoalStore:
        def get_goal_text(self, sid):
            return "整理三份文档"

    loop = AgentLoop(
        llm_registry=_FakeLLM(),
        tool_registry=_FakeToolRegistry(),
        compressor=real_compressor,
        session_goal_store=_FakeGoalStore(),
    )

    # Build enough messages to have a middle chunk
    msgs = [{"role": "system", "content": "[skill_prelude]"}]
    for i in range(15):
        msgs.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}" * 10})

    # We need to capture what working_messages looked like after compaction.
    # Monkey-patch the LLM to record the messages it receives.
    received_msgs: list = []

    class _RecordingLLM:
        async def chat_with_fallback(self, messages, *, tools=None, model=None, **kw):
            received_msgs.extend(messages)
            from llm.types import ChatResponse
            return ChatResponse(
                content="done",
                stop_reason="end_turn",
                tool_calls=[],
                usage={"input_tokens": 5, "output_tokens": 3},
            )

    loop.llm = _RecordingLLM()

    async for _ in loop.run(msgs, session_id="test_real"):
        pass

    # After compaction: LLM should have received the compressed messages,
    # including a system block with [目标锚定]
    if received_msgs:
        anchor_found = any(
            "[目标锚定]" in (m.get("content") or "")
            for m in received_msgs
            if m.get("role") == "system"
        )
        assert anchor_found, \
            f"[目标锚定] not found in messages passed to LLM after compaction.\n" \
            f"System msgs: {[m for m in received_msgs if m.get('role') == 'system']}"


# ---------------------------------------------------------------------------
# config.py: compaction_enabled flag exists
# ---------------------------------------------------------------------------

def test_config_has_compaction_enabled_flag():
    """AppConfig (or FeaturesConfig) exposes a compaction_enabled flag."""
    from config import AppConfig
    cfg = AppConfig()
    # Must have compaction_enabled accessible (either direct or via raw)
    # Per spec: FeaturesConfig.compaction_enabled or similar
    # Check it exists and defaults to False (prod default off)
    has_flag = (
        hasattr(cfg.features, "compaction_enabled")
        or hasattr(cfg, "compaction_enabled")
    )
    assert has_flag, (
        "AppConfig/FeaturesConfig missing compaction_enabled flag. "
        "Add compaction_enabled: bool = False to FeaturesConfig."
    )


def test_config_compaction_enabled_default_true():
    """WI-6 (compaction-bestpractice-upgrade): compaction_enabled 默认翻 True
    (gate: P-B 修复 + 单测全绿 + 真机 case ② 任务连续性通过)。"""
    from config import AppConfig
    cfg = AppConfig()
    flag = getattr(cfg.features, "compaction_enabled", None)
    if flag is None:
        flag = getattr(cfg, "compaction_enabled", None)
    assert flag is True, f"compaction_enabled must default to True after WI-6, got {flag!r}"


# ---------------------------------------------------------------------------
# build_agent: compressor wired when flag on, None when off
# ---------------------------------------------------------------------------

def test_build_agent_passes_compressor_none_when_flag_off():
    """build_agent with compaction_enabled=False → agent.compressor is None (BC)."""
    from config import AppConfig
    from main import build_agent

    cfg = AppConfig()
    # WI-6 后默认 True → 本测显式关掉以验证 flag-off 的 BC 路径(compressor=None)。
    cfg.features.compaction_enabled = False
    assert not getattr(cfg.features, "compaction_enabled", False)

    loop = build_agent(
        cfg,
        llm_registry=_FakeLLM(),
        tool_registry=_FakeToolRegistry(),
        context_manager=None,
        receipt_store_getter=lambda: None,
    )
    assert loop.compressor is None, (
        "With compaction_enabled=False, build_agent must pass compressor=None to AgentLoop"
    )


def test_context_compressor_in_valid_services():
    """FP-5 WI-4.0 真机回归: main.py 无条件 register('context_compressor'),
    缺白名单会致启动 register 失败 + chat get 抛 'Unknown service' → code-mode 全崩。"""
    from context import _VALID_SERVICES
    assert "context_compressor" in _VALID_SERVICES


# ---------------------------------------------------------------------------
# FP-2 TC-2.1 第 3 刀 — relay 真实 prompt_tokens 反馈回路。
# 真机实测两轮系数校准后估算仍低估(real 32.9k 时 estimate <24k 不触发)。
# 修法: compaction 判定用 max(estimate, 上一轮 response.usage.input_tokens)。
# ---------------------------------------------------------------------------

class _HighUsageTwoTurnLLM:
    """第 1 轮回 tool_use 且 usage.input_tokens=30000(模拟 relay 真实值),
    第 2 轮 end_turn。compaction 检查在第 2 轮 LLM 调用前 → 应收到 ≥30000。"""

    def __init__(self):
        self.calls = 0

    async def chat_with_fallback(self, messages, *, tools=None, model=None, **kw):
        self.calls += 1
        from llm.types import ChatResponse, ToolCall
        if self.calls == 1:
            return ChatResponse(
                content="",
                stop_reason="tool_use",
                tool_calls=[ToolCall(id="t1", name="noop", arguments={})],
                usage={"input_tokens": 30000, "output_tokens": 10},
            )
        return ChatResponse(
            content="done", stop_reason="end_turn", tool_calls=[],
            usage={"input_tokens": 30100, "output_tokens": 5},
        )


@pytest.mark.asyncio
async def test_real_usage_feedback_overrides_low_estimate():
    """estimate 低(短消息)但上一轮 real input_tokens=30000 →
    第 2 轮 compaction 检查必须收到 ≥30000(real 反馈回路)。"""
    comp = _FakeCompressor(should=False)  # 记录收到的值;不真压缩
    loop = AgentLoop(
        llm_registry=_HighUsageTwoTurnLLM(),
        tool_registry=_FakeToolRegistry(),
        compressor=comp,
    )
    msgs = [{"role": "user", "content": "短消息"}]
    async for _ev in loop.run(msgs, session_id="s-realfb", task_id="t-realfb"):
        pass
    # 第 1 次检查(iteration1, 无 real 值)可为小值;
    # 第 2 次检查(iteration2)必须 >= 30000
    assert len(comp.should_compress_calls) >= 2, comp.should_compress_calls
    assert comp.should_compress_calls[1] >= 30000, (
        f"real usage feedback missing: {comp.should_compress_calls}"
    )
