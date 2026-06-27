# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-4b 单测 — SelfCheckGate（Step6 异体自检整合 VerifyGate + 异体评分）。

覆盖（05 L1）：
  - 按 problem_type 选档：debug→strict / chitchat→off(skip) / factual_qa→light
  - VerifyGate 对账（mock，passed/unmatched）
  - 异体评分仅 strict + failure_count≥2 + heterogeneous on 才触发
  - chitchat 直接 pass（不调任何底层）
  - 底层全 None → pass（降级）
  - passed=False → reflection_instruction 非空
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from deskpet.agent.self_check_gate import SelfCheckGate, SelfCheckOutcome


def _verify_gate(passed: bool, unmatched: int = 0):
    vg = MagicMock()
    vg.mode = "strict"
    vg.check = MagicMock(return_value=SimpleNamespace(
        passed=passed, unmatched_claims=["c"] * unmatched,
    ))
    return vg


def test_mode_for_problem_types() -> None:
    gate = SelfCheckGate()
    assert gate.mode_for("debug") == "strict"
    assert gate.mode_for("creation") == "strict"
    assert gate.mode_for("research") == "light"
    assert gate.mode_for("factual_qa") == "light"
    assert gate.mode_for("chitchat") == "off"
    assert gate.mode_for("unknown_xyz") == "light"  # 默认 light


def test_chitchat_skips() -> None:
    vg = _verify_gate(passed=True)
    gate = SelfCheckGate(verify_gate=vg)
    out = asyncio.run(gate.check(problem_type="chitchat", assistant_text="嗨", ledger=[]))
    assert out.passed is True
    assert out.mode == "off"
    vg.check.assert_not_called()  # chitchat 不调对账


def test_verify_pass_no_heterogeneous() -> None:
    vg = _verify_gate(passed=True)
    ext = AsyncMock()
    gate = SelfCheckGate(verify_gate=vg, external_evaluator=ext)
    out = asyncio.run(gate.check(
        problem_type="debug", assistant_text="修好了", ledger=[],
        failure_count=0,
    ))
    assert out.passed is True
    assert out.heterogeneous is False
    ext.evaluate.assert_not_called()  # passed → 不触发异体


def test_verify_fail_triggers_reflection() -> None:
    vg = _verify_gate(passed=False, unmatched=2)
    gate = SelfCheckGate(verify_gate=vg, external_evaluator=None)
    out = asyncio.run(gate.check(
        problem_type="debug", assistant_text="假装修好了", ledger=[],
        failure_count=0,
    ))
    assert out.passed is False
    assert out.claims_unverified == 2
    assert out.reflection_instruction != ""  # 注入反思指令


def test_heterogeneous_triggers_on_strict_failure() -> None:
    """strict + failure_count≥2 + heterogeneous → 调异体评分。"""
    vg = _verify_gate(passed=False, unmatched=1)
    ext = MagicMock()
    ext.evaluate = AsyncMock(return_value={"verdict": "revise", "quality_score": 3})
    gate = SelfCheckGate(verify_gate=vg, external_evaluator=ext, heterogeneous_enabled=True)
    out = asyncio.run(gate.check(
        problem_type="creation", assistant_text="交付物", ledger=[],
        failure_count=2, produced_artifacts=["a.pptx"], objective_evidence=["ok"],
    ))
    assert out.heterogeneous is True
    assert out.passed is False
    ext.evaluate.assert_awaited_once()


def test_heterogeneous_not_triggered_when_disabled() -> None:
    vg = _verify_gate(passed=False, unmatched=1)
    ext = MagicMock()
    ext.evaluate = AsyncMock(return_value={"verdict": "revise", "quality_score": 3})
    gate = SelfCheckGate(verify_gate=vg, external_evaluator=ext, heterogeneous_enabled=False)
    out = asyncio.run(gate.check(
        problem_type="debug", assistant_text="x", ledger=[], failure_count=5,
    ))
    assert out.heterogeneous is False
    ext.evaluate.assert_not_called()


def test_all_none_degrades_to_pass() -> None:
    """底层全 None（无对账、无异体）→ pass（降级，不崩）。"""
    gate = SelfCheckGate(verify_gate=None, external_evaluator=None)
    out = asyncio.run(gate.check(problem_type="debug", assistant_text="x", ledger=[]))
    assert out.passed is True


def test_light_mode_no_heterogeneous() -> None:
    """light 档（research）即便失败也不调异体评分（异体仅 strict）。"""
    vg = _verify_gate(passed=False, unmatched=1)
    ext = MagicMock()
    ext.evaluate = AsyncMock(return_value={"verdict": "revise", "quality_score": 2})
    gate = SelfCheckGate(verify_gate=vg, external_evaluator=ext)
    out = asyncio.run(gate.check(
        problem_type="research", assistant_text="x", ledger=[], failure_count=5,
    ))
    assert out.mode == "light"
    assert out.heterogeneous is False
    ext.evaluate.assert_not_called()
