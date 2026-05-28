# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""TG-10 — Outcome Verifier 4 件套（WI-T2.5）。

PRD §3 D7 + D8 + TDD §B TG-10 T10-1~T10-9。
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from deskpet.agent.outcome_verifier import (
    BuildVerifier,
    FileExistsVerifier,
    GitDiffVerifier,
    OutcomeReport,
    TestVerifier,
    VerifierOutcome,
    run_outcome_verifiers,
)


# ─── T10-1/T10-2/T10-3 file_exists ──────────────────────────

def test_t10_1_file_exists_pass_when_file_present(tmp_path):
    f = tmp_path / "x.pptx"
    f.write_bytes(b"hello")
    out = FileExistsVerifier().verify(
        receipts=[],
        expected_paths_and_shas=[(str(f), None)],
    )
    assert out.status == "pass"


def test_t10_1_file_exists_fail_when_missing(tmp_path):
    out = FileExistsVerifier().verify(
        receipts=[],
        expected_paths_and_shas=[(str(tmp_path / "nope.pptx"), None)],
    )
    assert out.status == "fail"
    assert out.error_class == "missing_file"
    assert "missing=" in out.reason


def test_t10_2_file_exists_fail_when_size_zero(tmp_path):
    f = tmp_path / "empty.pptx"
    f.write_bytes(b"")
    out = FileExistsVerifier().verify(
        receipts=[],
        expected_paths_and_shas=[(str(f), None)],
    )
    assert out.status == "fail"
    assert "size_zero" in out.reason


def test_t10_3_file_exists_fail_on_sha_mismatch(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"actual content")
    expected_sha = hashlib.sha256(b"different content").hexdigest()
    out = FileExistsVerifier().verify(
        receipts=[],
        expected_paths_and_shas=[(str(f), expected_sha)],
    )
    assert out.status == "fail"
    assert "sha256_mismatch" in out.reason


def test_t10_3b_file_exists_pass_on_sha_match(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"hello")
    correct_sha = hashlib.sha256(b"hello").hexdigest()
    out = FileExistsVerifier().verify(
        receipts=[],
        expected_paths_and_shas=[(str(f), correct_sha)],
    )
    assert out.status == "pass"


# ─── T10-4 git_diff ──────────────────────────────────────────

def test_t10_4_git_diff_skipped_when_not_git_repo(tmp_path):
    out = GitDiffVerifier().verify(changed_files=["a.txt"], cwd=str(tmp_path))
    assert out.status == "skipped"
    assert out.reason == "not_a_git_repo"


# ─── T10-5/T10-6/T10-8 build + test + missing toolchain ────

def test_t10_5_build_skipped_when_no_python_files():
    out = BuildVerifier(kind="backend").verify(
        changed_files=["foo.md"], cwd=".",
    )
    assert out.status == "skipped"
    assert "no_python_files_changed" in out.reason


def test_t10_8_build_skipped_when_npm_missing(monkeypatch):
    """mock shutil.which to return None — verifier 必须优雅 skip。"""
    import deskpet.agent.outcome_verifier as ov
    monkeypatch.setattr(ov, "_which", lambda c: None if c in ("npx", "npm") else "/bin/x")
    out = BuildVerifier(kind="frontend").verify(
        changed_files=["foo.ts"], cwd=".",
    )
    assert out.status == "skipped"
    assert out.reason == "missing_npm"


def test_t10_6_test_skipped_when_pytest_missing(monkeypatch):
    import deskpet.agent.outcome_verifier as ov
    monkeypatch.setattr(ov, "_which", lambda c: None if c == "pytest" else "/bin/x")
    out = TestVerifier().verify(changed_files=["test_x.py"], cwd=".")
    assert out.status == "skipped"
    assert out.reason == "missing_pytest"


def test_t10_6b_test_skipped_when_no_test_files(monkeypatch):
    import deskpet.agent.outcome_verifier as ov
    monkeypatch.setattr(ov, "_which", lambda c: "/bin/pytest")
    out = TestVerifier().verify(changed_files=["app.py"], cwd=".")
    assert out.status == "skipped"
    assert "no_test_files_changed" in out.reason


# ─── T10-7 timeout 保护 ─────────────────────────────────────

def test_t10_7_build_timeout_does_not_crash(tmp_path, monkeypatch):
    """timeout 时 status=timeout，不 raise；不阻 end_turn 流程。"""
    import deskpet.agent.outcome_verifier as ov
    # Force "which python" to return a path so we reach subprocess.run
    monkeypatch.setattr(ov, "_which", lambda c: "/bin/python")
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")

    def _fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="py", timeout=60)
    monkeypatch.setattr(subprocess, "run", _fake_run)

    out = BuildVerifier(kind="backend").verify(
        changed_files=[str(f)], cwd=str(tmp_path),
    )
    assert out.status == "timeout"


# ─── run_outcome_verifiers entry point + OutcomeReport ─────

def test_t10_int_entry_point_no_change_no_verifiers_run(tmp_path):
    """No changed_files + no expected_paths → 空 report（不浪费 verifier 调用）。"""
    report = run_outcome_verifiers(
        receipts=[],
        changed_files=[],
        cwd=str(tmp_path),
    )
    assert isinstance(report, OutcomeReport)
    assert len(report.outcomes) == 0
    assert report.has_failures() is False


def test_t10_int_entry_point_runs_file_exists_when_paths_given(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"hi")
    report = run_outcome_verifiers(
        receipts=[],
        changed_files=[],
        cwd=str(tmp_path),
        expected_paths_and_shas=[(str(f), None)],
    )
    assert len(report.outcomes) == 1
    assert report.outcomes[0].verifier == "file_exists"
    assert report.outcomes[0].status == "pass"


def test_t10_int_feedback_message_d8_schema(tmp_path):
    """D8 回灌 system message schema."""
    report = OutcomeReport()
    report.outcomes.append(VerifierOutcome(
        verifier="file_exists", status="fail",
        reason="missing=['/tmp/x.pptx']",
        error_class="missing_file",
    ))
    msg = report.to_feedback_message()
    assert "[verify-gate]" in msg
    assert "[missing_file]" in msg
    assert "file_exists" in msg


# ─── T10-9 git_diff skip when git missing ──────────────────

def test_t10_9_git_diff_skipped_when_git_missing(monkeypatch, tmp_path):
    import deskpet.agent.outcome_verifier as ov
    monkeypatch.setattr(ov, "_which", lambda c: None)
    out = GitDiffVerifier().verify(changed_files=["x.py"], cwd=str(tmp_path))
    assert out.status == "skipped"
    assert out.reason == "missing_git"
