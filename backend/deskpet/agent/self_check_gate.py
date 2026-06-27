# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Step6 SelfCheckGate — 异体自检·反思（实践→认识→再实践 + 非执行者打分）。

编排现有三件（不重写，只调度 + 按 problem_type 选严格档）：
  - VerifyGate    : 声明 vs 凭据对账层（regex 抽 claim 对 receipt）
  - StructuredReflection: rebound 时强制 5 段 JSON 反思（注入 _REFLECTION_INSTRUCTION）
  - ExternalEvaluator   : 失败 N 次后异体子代理打分（非主 LLM 自评）

按 problem_type 选档（03 §3 Step6）：
  debug / creation → strict（对账 + 异体评分）
  research / multi_task → light（对账，异体仅在高后果触发）
  factual_qa → light（轻量对账）
  chitchat → off（跳过，不拖慢闲聊）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

# problem_type → 自检档位
_MODE_BY_PROBLEM = {
    "debug": "strict",
    "creation": "strict",
    "research": "light",
    "multi_task": "light",
    "factual_qa": "light",
    "ambiguous": "light",
    "chitchat": "off",
}


@dataclass
class SelfCheckOutcome:
    passed: bool = True
    mode: str = "off"
    heterogeneous: bool = False       # 是否动用了异体评分
    claims_unverified: int = 0
    reflection_instruction: str = ""  # passed=False 时要注入的反思指令（含 _REFLECTION_INSTRUCTION）
    reason: str = ""


class SelfCheckGate:
    """end_turn 前调用，整合 VerifyGate + reflection + ExternalEvaluator。

    flag off / problem_type=chitchat → 直接 pass（BC）。所有底层组件 None 时退化为 pass。
    """

    def __init__(
        self,
        *,
        verify_gate: Optional[Any] = None,            # deskpet.agent.verify_gate.VerifyGate
        external_evaluator: Optional[Any] = None,     # deskpet.agent.external_evaluator.ExternalEvaluator
        heterogeneous_enabled: bool = True,
        reflection_enabled: bool = True,
    ) -> None:
        self._verify_gate = verify_gate
        self._external_evaluator = external_evaluator
        self._heterogeneous_enabled = heterogeneous_enabled
        self._reflection_enabled = reflection_enabled

    def mode_for(self, problem_type: str) -> str:
        return _MODE_BY_PROBLEM.get(problem_type, "light")

    async def check(
        self,
        *,
        problem_type: str,
        assistant_text: str,
        ledger: list,
        goal_text: Optional[str] = None,
        failure_count: int = 0,
        produced_artifacts: Optional[list[str]] = None,
        objective_evidence: Optional[list[str]] = None,
    ) -> SelfCheckOutcome:
        mode = self.mode_for(problem_type)
        if mode == "off":
            return SelfCheckOutcome(passed=True, mode="off", reason="problem_type_off")

        # ── 层1：VerifyGate 对账（复用现有 check；verify_gate=None → pass）
        unmatched = 0
        verify_passed = True
        if self._verify_gate is not None and getattr(self._verify_gate, "mode", "off") != "off":
            try:
                outcome = self._verify_gate.check(
                    assistant_text=assistant_text, ledger=ledger, goal_text=goal_text,
                )
                verify_passed = bool(getattr(outcome, "passed", True))
                unmatched = len(getattr(outcome, "unmatched_claims", []) or [])
            except Exception as exc:  # noqa: BLE001 — safe-fail
                logger.warning("self_check.verify_failed", error=str(exc)[:200])

        # ── 层2：strict 档 + 失败累积达阈 → 异体评分（非执行者打分）
        heterogeneous = False
        evaluator_revise = False
        if (
            not verify_passed
            and mode == "strict"
            and self._heterogeneous_enabled
            and self._external_evaluator is not None
            and failure_count >= 2  # 与 VerifyGate.MAX_FAILURES_BEFORE_EPHEMERAL 对齐档
        ):
            try:
                heterogeneous = True
                ev = await self._external_evaluator.evaluate(
                    original_goal=goal_text or "",
                    produced_artifacts=produced_artifacts or [],
                    objective_evidence=objective_evidence or [],
                    conversation_summary=assistant_text[:512],
                )
                evaluator_revise = (
                    ev.get("verdict") == "revise" and ev.get("quality_score", 10) < 6
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("self_check.evaluator_failed", error=str(exc)[:200])

        passed = verify_passed and not evaluator_revise
        instr = ""
        if not passed and self._reflection_enabled:
            from deskpet.agent.reflection import _REFLECTION_INSTRUCTION  # noqa: PLC0415
            instr = _REFLECTION_INSTRUCTION

        logger.info("self_check.done", mode=mode, passed=passed,
                    heterogeneous=heterogeneous, unmatched=unmatched)
        return SelfCheckOutcome(
            passed=passed, mode=mode, heterogeneous=heterogeneous,
            claims_unverified=unmatched, reflection_instruction=instr,
            reason="" if passed else ("evaluator_revise" if evaluator_revise else "unmatched_claims"),
        )


__all__ = ["SelfCheckGate", "SelfCheckOutcome"]
