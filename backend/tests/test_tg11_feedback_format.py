# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""TG-11 — 失败反馈回灌格式（WI-T2.5 / WI-T2.6）。

PRD §3 D8 schema + 01-TDD.md §B TG-11 + ship-readiness round 1 残留。
"""
from __future__ import annotations

from deskpet.agent.outcome_verifier import (
    OutcomeReport,
    VerifierOutcome,
)


def test_t11_1_d8_schema_format():
    """T11-1: 回灌 system message 严格匹配 D8 schema。

    schema (PRD D8):
      [verify-gate] outcome verifiers report:
        N. [error_class] verifier — reason
           last 20 lines:
           <log_tail>
    """
    report = OutcomeReport(outcomes=[
        VerifierOutcome(
            verifier="file_exists",
            status="fail",
            reason="missing=['C:\\\\out\\\\x.pptx']",
            error_class="missing_file",
        ),
        VerifierOutcome(
            verifier="build",
            status="fail",
            reason="tsc_failed",
            error_class="build_error",
            log_tail="src/foo.ts(10,5): error TS2304: Cannot find name 'X'.\n"
                     "Found 1 error",
        ),
    ])
    msg = report.to_feedback_message()
    # 必有 header
    assert msg.startswith("[verify-gate]")
    # 必含分类标签
    assert "[missing_file]" in msg
    assert "[build_error]" in msg
    # 失败 verifier 必含 log_tail 段
    assert "last 20 lines:" in msg
    # 必含 reason 文本
    assert "tsc_failed" in msg


def test_t11_2_error_class_categories():
    """T11-2: 5 个新增错误分类全部可用（PRD D5 末段 + D8）。"""
    valid_classes = {
        "unmatched_claim", "missing_file", "build_error",
        "test_error", "hallucinated_claim",
    }
    for cls in valid_classes:
        o = VerifierOutcome(
            verifier="test_v", status="fail", reason="x", error_class=cls,
        )
        report = OutcomeReport(outcomes=[o])
        msg = report.to_feedback_message()
        assert f"[{cls}]" in msg


def test_t11_3_skipped_outcomes_omit_log_tail():
    """T11-3 corollary: skipped verifier 不输出 'last 20 lines:' 段
    （避免污染回灌消息长度）。"""
    report = OutcomeReport(outcomes=[
        VerifierOutcome(
            verifier="build", status="skipped", reason="missing_npm",
        ),
    ])
    msg = report.to_feedback_message()
    assert "[skipped]" in msg
    assert "last 20 lines:" not in msg


def test_t11_4_has_failures_detector():
    """T11-4 surface: has_failures() 用于决定是否回灌（PRD D8 触发条件）。"""
    all_pass = OutcomeReport(outcomes=[
        VerifierOutcome(verifier="file_exists", status="pass"),
        VerifierOutcome(verifier="build", status="skipped", reason="missing_npm"),
    ])
    assert all_pass.has_failures() is False

    with_fail = OutcomeReport(outcomes=[
        VerifierOutcome(verifier="file_exists", status="pass"),
        VerifierOutcome(
            verifier="build", status="fail", reason="x", error_class="build_error",
        ),
    ])
    assert with_fail.has_failures() is True


def test_t11_5_empty_report_emits_minimal_header():
    """边界：无 outcomes 时仍返回 header（caller 能 check has_failures()）。"""
    report = OutcomeReport(outcomes=[])
    msg = report.to_feedback_message()
    assert msg.startswith("[verify-gate]")
    assert report.has_failures() is False
