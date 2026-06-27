# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-1 单测 — ProblemHandlingPipeline 编排器（PRE-LOOP）。

覆盖：
  - flag off（kill-switch）全短路（intent_triage never called）
  - 闲聊短路：short_circuit=True，不注入 system 消息
  - 澄清出口：needs_clarification=True
  - 复杂问题：注入 <意图>+<主要矛盾>，发 chat_v2_intent + chat_v2_contradiction 事件
  - observability_events=False 时不发事件
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from deskpet.agent.intent_triage import (
    Contradiction, ContradictionMap, IntentCard, IntentTriage,
)
from deskpet.agent.problem_pipeline import ProblemHandlingPipeline, PreLoopResult


def _make_triage_returning(card: IntentCard) -> IntentTriage:
    triage = IntentTriage(None)
    triage.analyze = AsyncMock(return_value=card)  # type: ignore[method-assign]
    return triage


def test_flag_off_short_circuits_everything() -> None:
    triage = IntentTriage(None)
    triage.analyze = AsyncMock()  # type: ignore[method-assign]
    pipe = ProblemHandlingPipeline(enabled=False, intent_triage=triage)
    res = asyncio.run(pipe.run_pre_loop("任何消息", prior_task_type="code"))
    assert isinstance(res, PreLoopResult)
    assert res.short_circuit is False
    assert res.intent is None
    triage.analyze.assert_not_called()  # flag off → 根本不调


def test_chitchat_short_circuit() -> None:
    card = IntentCard(problem_type="chitchat", short_circuit=True)
    pipe = ProblemHandlingPipeline(
        enabled=True, intent_triage=_make_triage_returning(card),
        observability_events=True,
    )
    res = asyncio.run(pipe.run_pre_loop("你好", prior_task_type="chat"))
    assert res.short_circuit is True
    assert res.system_injections == []          # 短路不注入
    # 短路前已发 intent 事件（observability on）
    assert any(e["type"] == "chat_v2_intent" for e in res.events)


def test_clarification_exit() -> None:
    card = IntentCard(
        problem_type="ambiguous", ambiguity_score=0.9,
        clarifying_questions=["A 还是 B？"], needs_clarification=True,
    )
    pipe = ProblemHandlingPipeline(enabled=True, intent_triage=_make_triage_returning(card))
    res = asyncio.run(pipe.run_pre_loop("那个", prior_task_type=None))
    assert res.needs_clarification is True
    assert res.intent is card


def test_complex_injects_intent_and_contradiction() -> None:
    cmap = ContradictionMap(
        contradictions=[Contradiction(id=1, desc="token 过期", aspect="认证")],
        principal=1, principal_aspect="认证链路", attack_order=[1],
        rationale="先修 token",
    )
    card = IntentCard(
        restated_intent="修复登录", problem_type="debug",
        needs_investigation=True, contradiction=cmap,
    )
    pipe = ProblemHandlingPipeline(
        enabled=True, intent_triage=_make_triage_returning(card),
        observability_events=True,
    )
    res = asyncio.run(pipe.run_pre_loop("登录报错", prior_task_type="code"))
    assert res.short_circuit is False
    assert res.needs_clarification is False
    # 注入 <意图> + <主要矛盾> 两条 system 消息
    assert len(res.system_injections) == 2
    assert any("<意图>" in s for s in res.system_injections)
    assert any("<主要矛盾>" in s for s in res.system_injections)
    assert res.contradiction is cmap
    # 发两个观测事件
    types = {e["type"] for e in res.events}
    assert "chat_v2_intent" in types
    assert "chat_v2_contradiction" in types


def test_observability_off_no_events() -> None:
    card = IntentCard(restated_intent="x", problem_type="factual_qa", contradiction=None)
    pipe = ProblemHandlingPipeline(
        enabled=True, intent_triage=_make_triage_returning(card),
        observability_events=False,
    )
    res = asyncio.run(pipe.run_pre_loop("查个事实", prior_task_type="recall"))
    assert res.events == []
    # factual_qa 无矛盾段 → 只注入 <意图>
    assert len(res.system_injections) == 1
    assert "<意图>" in res.system_injections[0]
