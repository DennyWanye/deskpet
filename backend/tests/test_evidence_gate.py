# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-4a 单测 — EvidenceGate（Step2 取证门控）。

覆盖（05 L1 + R1 回归）：
  - needs_investigation 且 evidence_gathered=False → BLOCK + nudge
  - evidence_gathered=True → 放行（R1：compaction 后 working_messages 变短不影响布尔）
  - needs_investigation=False → 不拦
  - max_nudges 耗尽 → 放行 + exhausted
  - is_investigative 白名单判定
"""
from __future__ import annotations

from deskpet.agent.evidence_gate import EvidenceGate, EvidenceDecision


def test_block_when_no_evidence() -> None:
    gate = EvidenceGate(max_nudges=2)
    dec = gate.check(needs_investigation=True, evidence_gathered=False, nudges_used=0)
    assert dec.blocked is True
    assert dec.nudge_count == 1
    assert "<调查>" in dec.nudge
    assert "没有调查就没有发言权" in dec.nudge


def test_pass_when_evidence_gathered() -> None:
    """R1：evidence_gathered=True 一律放行（布尔对 compaction 免疫）。"""
    gate = EvidenceGate(max_nudges=2)
    dec = gate.check(needs_investigation=True, evidence_gathered=True, nudges_used=0)
    assert dec.blocked is False
    assert dec.reason == "evidence_present"


def test_no_investigation_needed_passes() -> None:
    gate = EvidenceGate()
    dec = gate.check(needs_investigation=False, evidence_gathered=False, nudges_used=0)
    assert dec.blocked is False
    assert dec.reason == "no_investigation_needed"


def test_max_nudges_exhausted_passes() -> None:
    gate = EvidenceGate(max_nudges=2)
    dec = gate.check(needs_investigation=True, evidence_gathered=False, nudges_used=2)
    assert dec.blocked is False
    assert dec.exhausted is True
    assert dec.reason == "exhausted"


def test_is_investigative_whitelist() -> None:
    gate = EvidenceGate()
    assert gate.is_investigative("read") is True
    assert gate.is_investigative("grep") is True
    assert gate.is_investigative("web_search") is True
    assert gate.is_investigative("write_file") is False
    assert gate.is_investigative("ppt_create") is False


def test_custom_investigative_tools() -> None:
    gate = EvidenceGate(investigative_tools=["my_search"])
    assert gate.is_investigative("my_search") is True
    assert gate.is_investigative("read") is False  # 自定义覆盖默认白名单


def test_compaction_regression_boolean_immune() -> None:
    """R1 回归：模拟 compaction 后（history 被压缩、working_messages 变短），
    只要 evidence_gathered 布尔已置 True，就一律放行不误注入。"""
    gate = EvidenceGate(max_nudges=2)
    # 第一次取证置位后，无论 nudges_used 多少都放行
    dec = gate.check(needs_investigation=True, evidence_gathered=True, nudges_used=1)
    assert dec.blocked is False
