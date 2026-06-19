# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""FP-2 Task 2 — WI-1.3 TDD: history_compactor goal_text anchor + agent_loop
decision-point anchor.

Test matrix
-----------
1. test_compact_bc_when_goal_none
   compact_messages(goal_text=None) → output byte-identical to omitting kwarg.

2. test_compact_injects_anchor
   compact_messages(goal_text="整理纪要") → output contains a system message
   with "[目标锚定]" and "整理纪要".

3. test_compact_anchor_not_injected_when_should_not_compact
   Below threshold + goal_text set → early return with no anchor (BC path).

4. test_compact_anchor_not_injected_on_empty_range
   Above threshold but nothing to compact (all recent) + goal_text → no anchor.

5. test_compact_anchor_not_injected_on_summarize_fail
   summarize_fn raises + goal_text → returns original (BC safe-fail).

6. test_compact_anchor_not_injected_on_empty_summary
   summarize_fn returns "" + goal_text → returns original (BC safe-fail).

7. test_compact_anchor_message_format
   Verify exact message prefix "[目标锚定] 当前目标：" and suffix text.

8. test_compact_anchor_is_appended_after_inject_summary
   The anchor system message is the LAST message in the returned list.

9. test_agent_loop_goal_anchor_module_const
   _GOAL_ANCHOR_EVERY = 5 exists in agent_loop module.

10. test_agent_loop_injects_anchor_at_anchor_every_iteration
    Run a stub AgentLoop: verify a system message containing "[目标锚定]"
    appears in working_messages at iterations that are multiples of
    _GOAL_ANCHOR_EVERY when an active goal exists.

11. test_agent_loop_no_anchor_when_no_goal
    Same loop, no goal → no "[目标锚定]" message injected (BC).

12. test_agent_loop_anchor_deduped_same_iteration
    _last_anchor_iter bookkeeping: anchor message injected exactly once per
    qualifying iteration, not twice.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from agent.history_compactor import (
    DEFAULT_CHAR_THRESHOLD,
    DEFAULT_MESSAGE_THRESHOLD,
    compact_messages,
)


# ─────────────────────── helpers ───────────────────────


def _big_messages(
    *,
    count: int = DEFAULT_MESSAGE_THRESHOLD + 5,
    chars_each: int = 10,
    extra_system: int = 1,
) -> list[dict[str, Any]]:
    """Build a conversation that exceeds message threshold.

    Structure: ``extra_system`` system messages at head, then alternating
    user/assistant to fill ``count`` non-system messages.
    """
    msgs: list[dict[str, Any]] = []
    for i in range(extra_system):
        msgs.append({"role": "system", "content": f"system-{i}"})
    roles = ["user", "assistant"]
    for i in range(count):
        msgs.append({"role": roles[i % 2], "content": "x" * chars_each + f"-{i}"})
    return msgs


async def _fake_summarize(text: str) -> str:
    return "SUMMARY"


async def _raising_summarize(text: str) -> str:
    raise RuntimeError("LLM down")


async def _empty_summarize(text: str) -> str:
    return ""


# ─────────────────────── 1. BC when goal_text=None ───────────────────────


@pytest.mark.asyncio
async def test_compact_bc_when_goal_none():
    """goal_text=None must produce byte-identical output to omitting the kwarg."""
    msgs = _big_messages()
    result_with_none = await compact_messages(
        msgs,
        summarize_fn=_fake_summarize,
        goal_text=None,
    )
    result_without = await compact_messages(
        msgs,
        summarize_fn=_fake_summarize,
    )
    # Should be structurally identical
    assert result_with_none == result_without


# ─────────────────────── 2. Anchor injected when goal_text set ───────────────────────


@pytest.mark.asyncio
async def test_compact_injects_anchor():
    """goal_text='整理纪要' → output has a system message with [目标锚定] + goal text."""
    msgs = _big_messages()
    result = await compact_messages(
        msgs,
        summarize_fn=_fake_summarize,
        goal_text="整理纪要",
    )
    anchor_msgs = [
        m for m in result
        if m.get("role") == "system" and "[目标锚定]" in str(m.get("content", ""))
    ]
    assert anchor_msgs, "Expected at least one [目标锚定] system message"
    anchor_content = anchor_msgs[0]["content"]
    assert "整理纪要" in anchor_content


# ─────────────────────── 3. No anchor on early-return (should_compact=False) ───────────────────────


@pytest.mark.asyncio
async def test_compact_anchor_not_injected_when_should_not_compact():
    """Below threshold → should_compact=False → early return, no anchor injected."""
    # Only 2 messages — well below DEFAULT_MESSAGE_THRESHOLD
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    result = await compact_messages(
        msgs,
        summarize_fn=_fake_summarize,
        goal_text="some goal",
    )
    anchor_msgs = [
        m for m in result
        if m.get("role") == "system" and "[目标锚定]" in str(m.get("content", ""))
    ]
    assert anchor_msgs == [], "No anchor on below-threshold early-return"


# ─────────────────────── 4. No anchor on empty compact range ───────────────────────


@pytest.mark.asyncio
async def test_compact_anchor_not_injected_on_empty_range():
    """Above threshold but select_compactable_range returns empty → no anchor."""
    # All recent — keep_recent=999 means start>=end → no anchor either
    msgs = _big_messages(count=DEFAULT_MESSAGE_THRESHOLD + 1)
    result = await compact_messages(
        msgs,
        summarize_fn=_fake_summarize,
        goal_text="goal",
        keep_recent=1000,  # huge keep_recent → nothing to compact
    )
    anchor_msgs = [
        m for m in result
        if m.get("role") == "system" and "[目标锚定]" in str(m.get("content", ""))
    ]
    assert anchor_msgs == [], "No anchor when compact range is empty"


# ─────────────────────── 5. No anchor on summarize failure ───────────────────────


@pytest.mark.asyncio
async def test_compact_anchor_not_injected_on_summarize_fail():
    """summarize_fn raises → returns original list unchanged (no anchor)."""
    msgs = _big_messages()
    result = await compact_messages(
        msgs,
        summarize_fn=_raising_summarize,
        goal_text="goal on failure",
    )
    # Should be a shallow copy of original (no modifications)
    assert result == list(msgs)
    anchor_msgs = [
        m for m in result
        if "[目标锚定]" in str(m.get("content", ""))
    ]
    assert anchor_msgs == []


# ─────────────────────── 6. No anchor on empty summary ───────────────────────


@pytest.mark.asyncio
async def test_compact_anchor_not_injected_on_empty_summary():
    """summarize_fn returns '' → returns original list unchanged (no anchor)."""
    msgs = _big_messages()
    result = await compact_messages(
        msgs,
        summarize_fn=_empty_summarize,
        goal_text="goal on empty summary",
    )
    assert result == list(msgs)
    anchor_msgs = [
        m for m in result
        if "[目标锚定]" in str(m.get("content", ""))
    ]
    assert anchor_msgs == []


# ─────────────────────── 7. Exact anchor message format ───────────────────────


@pytest.mark.asyncio
async def test_compact_anchor_message_format():
    """Anchor message must start with '[目标锚定] 当前目标：' and contain goal text."""
    goal = "完成报告"
    msgs = _big_messages()
    result = await compact_messages(
        msgs,
        summarize_fn=_fake_summarize,
        goal_text=goal,
    )
    anchor_msgs = [
        m for m in result
        if m.get("role") == "system" and "[目标锚定]" in str(m.get("content", ""))
    ]
    assert anchor_msgs, "anchor message must exist"
    content = anchor_msgs[0]["content"]
    assert content.startswith("[目标锚定] 当前目标："), (
        f"Expected prefix '[目标锚定] 当前目标：', got: {content[:80]!r}"
    )
    assert goal in content
    # Must also contain the "don't drift" reminder text
    assert "不要被中间步骤带偏" in content


# ─────────────────────── 8. Anchor is LAST message ───────────────────────


@pytest.mark.asyncio
async def test_compact_anchor_is_appended_after_inject_summary():
    """The anchor system message must be the LAST element of the returned list."""
    msgs = _big_messages()
    result = await compact_messages(
        msgs,
        summarize_fn=_fake_summarize,
        goal_text="tail goal",
    )
    last_msg = result[-1]
    assert last_msg.get("role") == "system"
    assert "[目标锚定]" in str(last_msg.get("content", ""))


# ─────────────────────── 9. Module const _GOAL_ANCHOR_EVERY ───────────────────────


def test_agent_loop_goal_anchor_module_const():
    """_GOAL_ANCHOR_EVERY must be defined as 5 in agent_loop module."""
    from agent import agent_loop
    assert hasattr(agent_loop, "_GOAL_ANCHOR_EVERY"), (
        "_GOAL_ANCHOR_EVERY constant missing from agent_loop"
    )
    assert agent_loop._GOAL_ANCHOR_EVERY == 5


# ─────────────────────── agent_loop helpers ───────────────────────


def _make_final_response():
    from llm.types import ChatResponse, ChatUsage
    return ChatResponse(
        content="done",
        stop_reason="end_turn",
        usage=ChatUsage(input_tokens=5, output_tokens=3),
        model="test-model",
    )


def _make_tool_response(tool_name: str = "my_tool"):
    from llm.types import ChatResponse, ChatUsage, ToolCall
    return ChatResponse(
        content="",
        stop_reason="tool_use",
        tool_calls=[
            ToolCall(id="tc1", name=tool_name, arguments={"x": 1}),
        ],
        usage=ChatUsage(input_tokens=5, output_tokens=3),
        model="test-model",
    )


class _RecordingLLM:
    """LLM that records all messages it receives and returns programmed responses."""

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.all_messages: list[list[dict]] = []

    async def chat_with_fallback(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ):
        self.all_messages.append(list(messages))
        if not self._responses:
            raise AssertionError("_RecordingLLM ran out of responses")
        return self._responses.pop(0)


class _NullToolRegistry:
    def schemas(self, enabled_toolsets=None):
        return []

    def dispatch(self, name: str, args: dict, task_id: str) -> str:
        return '{"ok": true}'


# ─────────────────────── 10. anchor at anchor_every iterations ───────────────────────


@pytest.mark.asyncio
async def test_agent_loop_injects_anchor_at_anchor_every_iteration():
    """At iteration==_GOAL_ANCHOR_EVERY, a [目标锚定] system message must be
    injected into working_messages when goal is active."""
    from agent.agent_loop import AgentLoop, _GOAL_ANCHOR_EVERY
    from deskpet.agent.goal_store import SessionGoalStore

    store = SessionGoalStore()
    store.set("sess1", "整理会议纪要")

    # Build enough responses to reach iteration _GOAL_ANCHOR_EVERY
    # Each iteration before the anchor iteration: tool_use (to keep looping),
    # then on the anchor iteration itself: end_turn.
    # We need _GOAL_ANCHOR_EVERY iterations total.
    # Iterations 1..(N-1) → tool_use; iteration N → end_turn.
    # We arrange: iters 1..N-1 return tool_use, iter N returns end_turn.
    n = _GOAL_ANCHOR_EVERY  # 5
    responses = []
    for _ in range(n - 1):
        responses.append(_make_tool_response())
    responses.append(_make_final_response())

    llm = _RecordingLLM(responses)
    tools = _NullToolRegistry()

    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=tools,
        max_iterations=n + 2,
        session_goal_store=store,
    )

    events = [e async for e in loop.run(
        messages=[{"role": "user", "content": "go"}],
        session_id="sess1",
    )]

    # Check that at some point (around iteration _GOAL_ANCHOR_EVERY) a
    # [目标锚定] message was part of working_messages sent to the LLM.
    # The LLM records all messages it receives at each call.
    found_anchor = False
    for call_msgs in llm.all_messages:
        for m in call_msgs:
            if (
                m.get("role") == "system"
                and "[目标锚定]" in str(m.get("content", ""))
            ):
                found_anchor = True
                break
        if found_anchor:
            break

    assert found_anchor, (
        f"Expected [目标锚定] in at least one LLM call's messages. "
        f"Got {len(llm.all_messages)} calls. "
        f"Last call messages: {llm.all_messages[-1] if llm.all_messages else []}"
    )


# ─────────────────────── 11. no anchor when no goal ───────────────────────


@pytest.mark.asyncio
async def test_agent_loop_no_anchor_when_no_goal():
    """BC: session_goal_store=None or no active goal → no [目标锚定] injected."""
    from agent.agent_loop import AgentLoop, _GOAL_ANCHOR_EVERY

    n = _GOAL_ANCHOR_EVERY
    responses = []
    for _ in range(n - 1):
        responses.append(_make_tool_response())
    responses.append(_make_final_response())

    llm = _RecordingLLM(responses)
    tools = _NullToolRegistry()

    # No session_goal_store (BC path)
    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=tools,
        max_iterations=n + 2,
        session_goal_store=None,
    )

    events = [e async for e in loop.run(
        messages=[{"role": "user", "content": "go"}],
        session_id="sess_no_goal",
    )]

    for call_msgs in llm.all_messages:
        for m in call_msgs:
            assert "[目标锚定]" not in str(m.get("content", "")), (
                "Should NOT inject [目标锚定] when session_goal_store=None"
            )


# ─────────────────────── 12. anchor deduped same iteration ───────────────────────


@pytest.mark.asyncio
async def test_agent_loop_anchor_deduped_same_iteration():
    """Anchor message should appear at most once per qualifying iteration —
    the _last_anchor_iter guard prevents double-injection."""
    from agent.agent_loop import AgentLoop, _GOAL_ANCHOR_EVERY
    from deskpet.agent.goal_store import SessionGoalStore

    store = SessionGoalStore()
    store.set("sess_dedup", "dedup goal")

    n = _GOAL_ANCHOR_EVERY
    # Run enough that iteration n is reached and loop finishes after
    responses = []
    for _ in range(n - 1):
        responses.append(_make_tool_response())
    responses.append(_make_final_response())

    llm = _RecordingLLM(responses)
    tools = _NullToolRegistry()

    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=tools,
        max_iterations=n + 2,
        session_goal_store=store,
    )
    events = [e async for e in loop.run(
        messages=[{"role": "user", "content": "go"}],
        session_id="sess_dedup",
    )]

    # Count how many times [目标锚定] appears across ALL LLM calls at
    # the anchor iteration (call index n-1, 0-based).
    # We count DISTINCT injection positions: a message injected once will
    # appear in every subsequent LLM call (it accumulates in working_messages).
    # "deduped" means: for the call at exactly iteration n, the anchor was
    # added exactly once (not twice).
    # Strategy: check the FIRST call where [目标锚定] appears and count how
    # many times it appears in that single call's message list.
    for call_msgs in llm.all_messages:
        anchor_count = sum(
            1 for m in call_msgs
            if m.get("role") == "system" and "[目标锚定]" in str(m.get("content", ""))
        )
        if anchor_count > 0:
            assert anchor_count == 1, (
                f"Anchor injected {anchor_count} times in one call — expected exactly 1"
            )
            break
