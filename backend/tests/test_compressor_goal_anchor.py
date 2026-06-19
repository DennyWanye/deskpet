# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""TDD tests for WI-1.3 re-anchor: ContextCompressor goal_text injection."""
import pytest
from deskpet.agent.context_compressor import ContextCompressor


def _msgs(n=30):
    out = [{"role": "system", "content": "sys"}]
    for i in range(n):
        out.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}" * 50})
    return out


@pytest.mark.asyncio
async def test_compress_bc_when_goal_none(monkeypatch):
    """goal_text=None → output identical to not passing it at all (BC).
    Uses no-llm path so no LLM needed."""
    c = ContextCompressor(llm_registry=None)
    r1 = await c.compress(_msgs())
    r2 = await c.compress(_msgs(), goal_text=None)
    assert [m.get("content") for m in r1.messages] == [m.get("content") for m in r2.messages]


@pytest.mark.asyncio
async def test_compress_injects_goal_anchor_as_system():
    """With a working LLM, compress(msgs, goal_text=...) injects exactly one
    system message containing '[目标锚定]' and the goal text."""
    class _LLM:
        async def chat_with_fallback(self, *a, **k):
            class R:
                content = "SUMMARY"
            return R()

    c = ContextCompressor(llm_registry=_LLM())
    r = await c.compress(_msgs(), goal_text="整理三个会议纪要")
    anchor = [
        m for m in r.messages
        if m.get("role") == "system" and "[目标锚定]" in (m.get("content") or "")
    ]
    assert len(anchor) == 1
    assert "整理三个会议纪要" in anchor[0]["content"]


@pytest.mark.asyncio
async def test_goal_anchor_respects_soft_cap():
    """A huge goal_text is truncated; injected anchor block << raw input size."""
    class _LLM:
        async def chat_with_fallback(self, *a, **k):
            class R:
                content = "S"
            return R()

    c = ContextCompressor(llm_registry=_LLM())
    huge = "X" * 20000
    r = await c.compress(_msgs(), goal_text=huge, pending_tasks=["t1", "t2"])
    inj = "".join(
        m["content"]
        for m in r.messages
        if m.get("role") == "system" and "[目标锚定]" in (m.get("content") or "")
    )
    # soft cap ~1500 tokens ≈ 6000 chars; injected block must be << 20000 chars
    assert len(inj) < 8000


@pytest.mark.asyncio
async def test_compress_bc_no_middle_goal_none():
    """No-middle early return: goal_text=None → same as no goal_text arg."""
    # Provide fewer messages so middle_chunk is empty (≤ first_n+last_n)
    c = ContextCompressor(llm_registry=None, first_n=3, last_n=6)
    few_msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ho"}]
    r1 = await c.compress(few_msgs)
    r2 = await c.compress(few_msgs, goal_text=None)
    assert [m.get("content") for m in r1.messages] == [m.get("content") for m in r2.messages]


@pytest.mark.asyncio
async def test_compress_bc_empty_goal_string():
    """Empty string for goal_text → treated as None, nothing injected (BC)."""
    class _LLM:
        async def chat_with_fallback(self, *a, **k):
            class R:
                content = "SUMMARY"
            return R()

    c = ContextCompressor(llm_registry=_LLM())
    r_none = await c.compress(_msgs(), goal_text=None)
    r_empty = await c.compress(_msgs(), goal_text="")
    anchor_none = [m for m in r_none.messages if m.get("role") == "system" and "[目标锚定]" in (m.get("content") or "")]
    anchor_empty = [m for m in r_empty.messages if m.get("role") == "system" and "[目标锚定]" in (m.get("content") or "")]
    assert len(anchor_none) == 0
    assert len(anchor_empty) == 0


@pytest.mark.asyncio
async def test_compress_includes_pending_task_in_anchor():
    """When pending_tasks is non-empty, anchor includes '[当前子目标]'."""
    class _LLM:
        async def chat_with_fallback(self, *a, **k):
            class R:
                content = "SUMMARY"
            return R()

    c = ContextCompressor(llm_registry=_LLM())
    r = await c.compress(_msgs(), goal_text="写周报", pending_tasks=["先收集数据", "再汇总"])
    anchor = [
        m for m in r.messages
        if m.get("role") == "system" and "[目标锚定]" in (m.get("content") or "")
    ]
    assert len(anchor) == 1
    assert "[当前子目标]" in anchor[0]["content"]
    assert "先收集数据" in anchor[0]["content"]
