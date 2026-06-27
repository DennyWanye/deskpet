# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-4c 单测 — ConvergenceController（Step7 收敛止损）。

覆盖（05 L1 + MINOR③ 回归）：
  - 量化收敛判据：principal 解 + 无未对账 + 资源未触顶 → converged
  - 资源触顶 → should_stop_loss + 止损报告
  - MINOR③：reason ∈ {permanent_tool_error, all_providers_failed} → resource_capped=True
    （旧硬编码集合会漏判）；reason ∈ {success,running,user_interrupted} → resource_capped=False
  - 未收敛但资源未触顶 → 继续（不止损）
"""
from __future__ import annotations

from unittest.mock import MagicMock

from deskpet.agent.convergence_controller import ConvergenceController, ConvergenceVerdict


def _ctrl(report_on_stop: bool = True) -> ConvergenceController:
    gate = MagicMock()
    return ConvergenceController(gate, report_on_stop=report_on_stop)


def test_converged_when_resolved_and_running() -> None:
    ctrl = _ctrl()
    v = ctrl.evaluate(principal_resolved=True, unverified_claims=0,
                      gate_summary={"reason": "running"})
    assert v.converged is True
    assert v.stop_reason == "success"
    assert v.should_stop_loss is False


def test_success_reason_is_converged() -> None:
    ctrl = _ctrl()
    v = ctrl.evaluate(principal_resolved=True, unverified_claims=0,
                      gate_summary={"reason": "success"})
    assert v.converged is True


def test_unverified_claims_block_convergence() -> None:
    ctrl = _ctrl()
    v = ctrl.evaluate(principal_resolved=True, unverified_claims=3,
                      gate_summary={"reason": "running"})
    assert v.converged is False
    assert v.should_stop_loss is False  # 资源未触顶 → 继续


def test_resource_capped_stop_loss_with_report() -> None:
    ctrl = _ctrl()
    v = ctrl.evaluate(principal_resolved=False, unverified_claims=0,
                      gate_summary={"reason": "error_max_turns",
                                    "turns_used": 4, "tools_used": 10})
    assert v.converged is False
    assert v.should_stop_loss is True
    assert "<收敛>" in v.report
    assert "error_max_turns" in v.report


def test_minor3_permanent_tool_error_is_capped() -> None:
    """MINOR③：permanent_tool_error 必须算触顶（旧硬编码 startswith error_ 会漏）。"""
    ctrl = _ctrl()
    v = ctrl.evaluate(principal_resolved=False, unverified_claims=0,
                      gate_summary={"reason": "permanent_tool_error"})
    assert v.should_stop_loss is True


def test_minor3_all_providers_failed_is_capped() -> None:
    ctrl = _ctrl()
    v = ctrl.evaluate(principal_resolved=False, unverified_claims=0,
                      gate_summary={"reason": "all_providers_failed"})
    assert v.should_stop_loss is True


def test_minor3_hallucination_is_capped() -> None:
    ctrl = _ctrl()
    v = ctrl.evaluate(principal_resolved=False, unverified_claims=0,
                      gate_summary={"reason": "hallucination"})
    assert v.should_stop_loss is True


def test_user_interrupted_not_capped() -> None:
    ctrl = _ctrl()
    v = ctrl.evaluate(principal_resolved=False, unverified_claims=0,
                      gate_summary={"reason": "user_interrupted"})
    assert v.should_stop_loss is False  # 用户中断不是触顶


def test_report_suppressed_when_disabled() -> None:
    ctrl = _ctrl(report_on_stop=False)
    v = ctrl.evaluate(principal_resolved=False, unverified_claims=0,
                      gate_summary={"reason": "error_max_turns"})
    assert v.should_stop_loss is True
    assert v.report == ""  # 关报告 → 空


def test_summary_fallback_to_gate() -> None:
    """gate_summary=None → 从 self._gate.summary() 取。"""
    gate = MagicMock()
    gate.summary.return_value = {"reason": "running"}
    ctrl = ConvergenceController(gate)
    v = ctrl.evaluate(principal_resolved=True, unverified_claims=0)
    assert v.converged is True
    gate.summary.assert_called()
