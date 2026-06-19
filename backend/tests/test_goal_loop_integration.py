# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-B3 — AgentLoop x SessionGoalStore + GoalChecker wiring tests.

Mirrors the shape of ``test_agent_loop_verify_wiring.py`` — checks the
__init__ signature, BC default (None / None → skip), the rebound
behaviour when ``goal.done is False``, and the ``mark_done`` path.
"""
from __future__ import annotations

import inspect
from typing import Any, Optional

import pytest

from agent.agent_loop import AgentLoop, FinalEvent
from deskpet.agent.goal_store import SessionGoalStore
from llm.types import ChatResponse, ChatUsage


# ───────────────────── fake LLM / tools (copied from existing loop tests) ─

class FakeLLMRegistry:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def chat_with_fallback(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.calls.append({"messages": messages, "tools": tools, "model": model})
        if not self._responses:
            raise AssertionError("FakeLLMRegistry ran out of programmed responses")
        return self._responses.pop(0)


class FakeToolRegistry:
    def schemas(self, enabled_toolsets: Optional[list[str]] = None) -> list[dict[str, Any]]:
        return []

    def dispatch(self, name: str, args: dict[str, Any], task_id: str) -> Any:
        raise KeyError(name)


class FakeGoalChecker:
    """Records every check call and returns the queued (done, hint) tuple."""

    def __init__(self, results: list[tuple[bool, str]]) -> None:
        self._results = list(results)
        self.calls: list[dict[str, Any]] = []

    async def check(
        self,
        goal_text: str,
        working_msgs: list[dict[str, Any]],
    ) -> tuple[bool, str]:
        self.calls.append({"goal_text": goal_text, "n_msgs": len(working_msgs)})
        if not self._results:
            raise AssertionError("FakeGoalChecker ran out of results")
        return self._results.pop(0)


# ───────────────────── tests ─────────────────────────────────────────────


def test_agent_loop_signature_accepts_goal_kwargs():
    """新 kwargs BC: 默认 None。"""
    sig = inspect.signature(AgentLoop.__init__)
    params = sig.parameters
    assert "session_goal_store" in params
    assert params["session_goal_store"].default is None
    assert "goal_checker" in params
    assert params["goal_checker"].default is None


@pytest.mark.asyncio
async def test_no_goal_store_skips_block_bc():
    """store=None → goal-check block 完全跳过 (BC, 现有 callers 不影响)."""
    llm = FakeLLMRegistry([
        ChatResponse(
            content="all done.",
            stop_reason="end_turn",
            usage=ChatUsage(input_tokens=1, output_tokens=1),
            model="x",
        )
    ])
    checker = FakeGoalChecker([])  # 不会被调
    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=FakeToolRegistry(),
        session_goal_store=None,  # 显式 BC
        goal_checker=checker,
    )
    events = [
        e async for e in loop.run(
            messages=[{"role": "user", "content": "hi"}],
            session_id="sid-1",
        )
    ]
    assert any(isinstance(e, FinalEvent) for e in events)
    assert checker.calls == []  # 未调用


@pytest.mark.asyncio
async def test_no_checker_skips_block_bc():
    """checker=None → 整块跳过."""
    llm = FakeLLMRegistry([
        ChatResponse(
            content="done.",
            stop_reason="end_turn",
            usage=ChatUsage(input_tokens=1, output_tokens=1),
            model="x",
        )
    ])
    store = SessionGoalStore()
    store.set("sid-1", "some goal")
    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=FakeToolRegistry(),
        session_goal_store=store,
        goal_checker=None,
    )
    events = [
        e async for e in loop.run(
            messages=[{"role": "user", "content": "hi"}],
            session_id="sid-1",
        )
    ]
    assert any(isinstance(e, FinalEvent) for e in events)
    # store 还在但 iterations 没被动
    g = store.get("sid-1")
    assert g is not None and g.iterations_used == 0 and g.done is False


@pytest.mark.asyncio
async def test_no_active_goal_skips_block():
    """store 存在但当前 session 无 goal → 不调 checker."""
    llm = FakeLLMRegistry([
        ChatResponse(
            content="done.",
            stop_reason="end_turn",
            usage=ChatUsage(input_tokens=1, output_tokens=1),
            model="x",
        )
    ])
    store = SessionGoalStore()
    checker = FakeGoalChecker([])
    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=FakeToolRegistry(),
        session_goal_store=store,
        goal_checker=checker,
    )
    events = [
        e async for e in loop.run(
            messages=[{"role": "user", "content": "hi"}],
            session_id="sid-no-goal",
        )
    ]
    assert any(isinstance(e, FinalEvent) for e in events)
    assert checker.calls == []


@pytest.mark.asyncio
async def test_goal_done_marks_and_finalizes():
    """checker 返 done=True → store.mark_done + 正常 emit FinalEvent."""
    llm = FakeLLMRegistry([
        ChatResponse(
            content="finished the task.",
            stop_reason="end_turn",
            usage=ChatUsage(input_tokens=1, output_tokens=1),
            model="x",
        )
    ])
    store = SessionGoalStore()
    store.set("sid-1", "write hello")
    checker = FakeGoalChecker([(True, "")])
    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=FakeToolRegistry(),
        session_goal_store=store,
        goal_checker=checker,
    )
    events = [
        e async for e in loop.run(
            messages=[{"role": "user", "content": "write hello"}],
            session_id="sid-1",
        )
    ]
    assert len(checker.calls) == 1
    assert checker.calls[0]["goal_text"] == "write hello"
    g = store.get("sid-1")
    assert g is not None and g.done is True
    assert g.iterations_used == 0  # done path 不 +=1
    # 应 emit FinalEvent
    assert any(isinstance(e, FinalEvent) for e in events)


@pytest.mark.asyncio
async def test_goal_not_done_rebounds_loop():
    """checker 返 done=False → AgentLoop 注入 system msg + continue;
    下一轮 LLM 返 end_turn 后第二次 checker 返 done=True 收尾."""
    llm = FakeLLMRegistry([
        # turn 1: assistant 说"我做好了"，但 goal 没真达成
        ChatResponse(
            content="我先 stub 了一下。",
            stop_reason="end_turn",
            usage=ChatUsage(input_tokens=1, output_tokens=1),
            model="x",
        ),
        # turn 2: 被 rebound 后 assistant 重新做
        ChatResponse(
            content="好的，现在真做完了。",
            stop_reason="end_turn",
            usage=ChatUsage(input_tokens=1, output_tokens=1),
            model="x",
        ),
    ])
    store = SessionGoalStore()
    store.set("sid-1", "ship the feature")
    checker = FakeGoalChecker([
        (False, "你只写了 stub, 没真做"),
        (True, ""),
    ])
    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=FakeToolRegistry(),
        session_goal_store=store,
        goal_checker=checker,
    )
    events = [
        e async for e in loop.run(
            messages=[{"role": "user", "content": "ship it"}],
            session_id="sid-1",
        )
    ]
    # checker 被调 2 次
    assert len(checker.calls) == 2
    g = store.get("sid-1")
    assert g is not None
    # 第一次 rebound +=1, 第二次 done=True (不 +=)
    assert g.iterations_used == 1
    assert g.done is True
    # 第二次 LLM call messages 里应能看到 [goal] system msg
    second_msgs = llm.calls[1]["messages"]
    assert any(
        m.get("role") == "system"
        and "[goal]" in str(m.get("content", ""))
        and "你只写了 stub" in str(m.get("content", ""))
        for m in second_msgs
    )
    # 最终 emit FinalEvent
    assert any(isinstance(e, FinalEvent) for e in events)


@pytest.mark.asyncio
async def test_goal_max_iterations_caps_rebounds():
    """iterations_used >= max → 不再 rebound, 直接 emit final."""
    llm = FakeLLMRegistry([
        ChatResponse(
            content="i give up",
            stop_reason="end_turn",
            usage=ChatUsage(input_tokens=1, output_tokens=1),
            model="x",
        )
    ])
    store = SessionGoalStore()
    goal = store.set("sid-1", "ship", max_iterations=2)
    # 提前把 iterations_used 推到 max → 模拟"已经 rebound 过 2 次"
    goal.iterations_used = 2
    checker = FakeGoalChecker([])  # 不应被调
    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=FakeToolRegistry(),
        session_goal_store=store,
        goal_checker=checker,
    )
    events = [
        e async for e in loop.run(
            messages=[{"role": "user", "content": "ship"}],
            session_id="sid-1",
        )
    ]
    assert checker.calls == []  # 上限触达 → 跳过
    assert any(isinstance(e, FinalEvent) for e in events)


@pytest.mark.asyncio
async def test_goal_already_done_skips_checker():
    """goal.done=True → 不再调 checker."""
    llm = FakeLLMRegistry([
        ChatResponse(
            content="done done",
            stop_reason="end_turn",
            usage=ChatUsage(input_tokens=1, output_tokens=1),
            model="x",
        )
    ])
    store = SessionGoalStore()
    store.set("sid-1", "x")
    store.mark_done("sid-1")
    checker = FakeGoalChecker([])
    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=FakeToolRegistry(),
        session_goal_store=store,
        goal_checker=checker,
    )
    events = [
        e async for e in loop.run(
            messages=[{"role": "user", "content": "x"}],
            session_id="sid-1",
        )
    ]
    assert checker.calls == []
    assert any(isinstance(e, FinalEvent) for e in events)


@pytest.mark.asyncio
async def test_checker_exception_safe_fails_through():
    """R-T3 §15.4 变更：checker.check raise → goal_check=skipped；不阻 final；
    但目标保持 active（不 mark_done）。

    旧行为：safe-fail → done=True → mark_done（危险：checker 故障时静默完成目标）。
    新行为：safe-fail → goal_check=skipped → 不 mark_done → goal 保持 active，
    但 AgentLoop 仍 fall-through 到 FinalEvent（不循环 / 不阻塞）。
    这防止了 checker 故障时的"幽灵完成"，同时不阻断正常 dispatch。
    """

    class _BrokenChecker:
        async def check(self, goal_text, working_msgs):
            raise RuntimeError("boom")

    llm = FakeLLMRegistry([
        ChatResponse(
            content="hmm",
            stop_reason="end_turn",
            usage=ChatUsage(input_tokens=1, output_tokens=1),
            model="x",
        )
    ])
    store = SessionGoalStore()
    store.set("sid-1", "g")
    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=FakeToolRegistry(),
        session_goal_store=store,
        goal_checker=_BrokenChecker(),
    )
    events = [
        e async for e in loop.run(
            messages=[{"role": "user", "content": "g"}],
            session_id="sid-1",
        )
    ]
    # R-T3: checker degraded → NOT mark_done (goal stays active)
    g = store.get("sid-1")
    assert g is not None and g.done is False, (
        "goal_check=skipped must NOT mark goal as done — "
        "checker failure should not silently complete the goal"
    )
    # But FinalEvent still emitted (checker skipped doesn't block dispatch)
    assert any(isinstance(e, FinalEvent) for e in events)
