# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Step7 ConvergenceController — 回用户·判收敛·止损（到群众中去 + 胸中有数 + 集中优势兵力）。

薄封装 TerminationGate（硬上限不重写）+ 加量化收敛判据 + 触顶止损报告。
不新开循环——只在 agent_loop 收尾判定点提供「是否收敛 / 该不该止损 / 止损报告文本」。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class ConvergenceVerdict:
    converged: bool
    principal_resolved: bool
    stop_reason: str
    report: str = ""               # 触顶未收敛时的诚实止损报告
    should_stop_loss: bool = False  # True → 不硬撑，产出 report 收尾


class ConvergenceController:
    """flag off 时调用方传 None，agent_loop 走原 TerminationGate.record_final_answer()（BC）。"""

    def __init__(
        self,
        termination_gate: Any,        # 复用 agent_loop 已构造的 self._gate
        *,
        report_on_stop: bool = True,
    ) -> None:
        self._gate = termination_gate
        self._report_on_stop = report_on_stop

    def evaluate(
        self,
        *,
        principal_resolved: bool,
        unverified_claims: int,
        gate_summary: Optional[dict] = None,
    ) -> ConvergenceVerdict:
        """量化收敛判据（胸中有数）：主要矛盾解 + 无未对账声明 + 资源未触顶。"""
        summary = gate_summary or (self._gate.summary() if self._gate is not None else {})
        reason = str(summary.get("reason", "running"))
        # 资源触顶判据（⚠️ round-3 MINOR③ 修正：硬编码触顶 reason 集合不全 → 改鲁棒补集）。
        # 已核实 termination.py:30-52 `TerminationReason` 全集 + :242-254 `summary()`：
        #   正常收尾/未触顶的 reason 只有 3 个：success / user_interrupted / running（未 terminate 合成）。
        #   其余全是触顶/错误态：error_max_turns / error_tool_budget / error_wall_clock_exceeded /
        #   error_max_budget_usd / permanent_tool_error / all_providers_failed / context_budget_block /
        #   hallucination / circuit_breaker_open。
        # 改成"非正常收尾即触顶"的补集形式，对 termination.py 未来新增触顶 reason 也鲁棒（默认归类触顶）。
        _NON_CAPPED_REASONS = ("running", "success", "user_interrupted")
        resource_capped = reason not in _NON_CAPPED_REASONS

        converged = principal_resolved and unverified_claims == 0 and not resource_capped

        if converged:
            return ConvergenceVerdict(
                converged=True, principal_resolved=True, stop_reason="success",
            )

        # 未收敛 + 资源触顶 → 止损（不硬撑）
        if resource_capped:
            report = self._build_report(principal_resolved, unverified_claims, summary) \
                if self._report_on_stop else ""
            logger.warning("convergence.stop_loss", reason=reason,
                           principal_resolved=principal_resolved, unverified=unverified_claims)
            return ConvergenceVerdict(
                converged=False, principal_resolved=principal_resolved,
                stop_reason=reason, report=report, should_stop_loss=True,
            )

        # 未收敛但资源未触顶 → 继续（不止损）
        return ConvergenceVerdict(
            converged=False, principal_resolved=principal_resolved, stop_reason="running",
        )

    def _build_report(self, principal_resolved: bool, unverified: int, summary: dict) -> str:
        return (
            "<收敛>\n"
            f"主要矛盾是否解决：{'是' if principal_resolved else '否'}\n"
            f"未对账声明数：{unverified}\n"
            f"已用轮数/工具数：{summary.get('turns_used', '?')}/{summary.get('tools_used', '?')}\n"
            f"止损原因：{summary.get('reason', '?')}\n"
            "建议下一步：上述卡点需要补充信息或换思路，下次可从「主要矛盾」处继续。"
        )


__all__ = ["ConvergenceController", "ConvergenceVerdict"]
