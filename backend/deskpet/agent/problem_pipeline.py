# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""ProblemHandlingPipeline — 七步问题处理流水线编排器（薄，仅 Companion 主线）。

只做：按 problem_type 决定哪几步跑 + 串接 PRE-LOOP（Step1+3 合并预分析 + Step4 方案）+
发标签事件 + flag 短路。真活由各组件干（IntentTriage[含矛盾]/plan.py/
EvidenceGate/SelfCheckGate/ConvergenceController）。

决策4（round-3）：Step1 意图 + Step3 主要矛盾**合并为 IntentTriage.analyze() 一次 LLM 调用**——
编排器不再持有独立 ContradictionAnalyzer，矛盾段直接从 IntentCard.contradiction 读取。

IN-LOOP 三闸（EvidenceGate/SelfCheckGate/ConvergenceController）由本编排器/build_agent 构造好后
**注入 AgentLoop**，在 loop 内被调用（见 agent_loop.py 改造）。本类只负责 PRE-LOOP 编排
+ 把 in-loop 组件交给 build_agent。

enabled=False（kill-switch）→ run_pre_loop 直接返回空结果，main.py 走今天的链路（回退）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import structlog

from deskpet.agent.intent_triage import (
    ContradictionMap, IntentCard, IntentTriage,
    contradiction_to_system_message, intent_to_system_message,
)

logger = structlog.get_logger(__name__)


@dataclass
class PreLoopResult:
    """PRE-LOOP 产出。main.py 据此注入 system 消息 + 发事件 + 决定是否短路/澄清。"""
    short_circuit: bool = False           # 闲聊短路：整条流水线跳过，裸 ReAct
    needs_clarification: bool = False     # 歧义高：暂停问澄清（走独立 chat_v2_final 澄清出口）
    intent: Optional[IntentCard] = None
    contradiction: Optional[ContradictionMap] = None
    system_injections: list[str] = field(default_factory=list)  # 注入 _msgs 的 system 文本（按序）
    events: list[dict] = field(default_factory=list)            # 要发的 WS 事件（type+payload）


class ProblemHandlingPipeline:
    def __init__(
        self,
        *,
        enabled: bool = False,
        intent_triage: Optional[IntentTriage] = None,
        observability_events: bool = False,
    ) -> None:
        self.enabled = enabled
        self._intent = intent_triage   # 决策4：单一合并预分析器（含矛盾）
        self._obs_events = observability_events

    async def run_pre_loop(
        self,
        user_message: str,
        *,
        prior_task_type: Optional[str] = None,
    ) -> PreLoopResult:
        """PRE-LOOP 编排（决策4：Step1+3 一次调用）：analyze → (短路/澄清出口) → 读 contradiction 段。
        Step4 plan 由 main.py 现有 plan 调用点处理（吃 attack_order，见 M1/M3）。
        """
        if not self.enabled or self._intent is None:
            return PreLoopResult()  # 回退：flag off → 空结果

        res = PreLoopResult()

        # ── Step1+3 合并预分析：一次 LLM 调用同时出 intent + (复杂问题的) contradiction
        card = await self._intent.analyze(user_message, prior_task_type=prior_task_type)
        res.intent = card
        if self._obs_events:
            res.events.append({
                "type": "chat_v2_intent",
                "payload": {
                    "restated_intent": card.restated_intent,
                    "problem_type": card.problem_type,
                    "ambiguity_score": card.ambiguity_score,
                },
            })

        if card.short_circuit:
            res.short_circuit = True
            logger.info("pipeline.short_circuit", problem_type=card.problem_type)
            return res

        if card.needs_clarification:
            res.needs_clarification = True
            return res  # main.py 走独立 chat_v2_final 澄清出口（emit 澄清问题 + 显式收尾 + 暂停）

        res.system_injections.append(intent_to_system_message(card))

        # ── Step3 主要矛盾：决策4 后无独立 LLM 调用，直接读 analyze 已产出的 contradiction 段
        cmap = card.contradiction
        if cmap is not None:
            res.contradiction = cmap
            res.system_injections.append(contradiction_to_system_message(cmap))
            if self._obs_events:
                res.events.append({
                    "type": "chat_v2_contradiction",
                    "payload": {
                        "principal": cmap.principal,
                        "attack_order": cmap.attack_order,
                        "rationale": cmap.rationale,
                    },
                })

        logger.info("pipeline.pre_loop_done",
                    injections=len(res.system_injections), events=len(res.events))
        return res


__all__ = ["ProblemHandlingPipeline", "PreLoopResult"]
