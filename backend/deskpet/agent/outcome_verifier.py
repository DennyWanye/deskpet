"""WI-T2.5 — Outcome Verifier 4 件套（PRD §3 D7）。

声明"会动文件 / 会跑测试 / 会改代码"的工具调用后，自动跑校验：
  - file_exists: path 真实存在 + size>0 + sha256 匹配 receipt
  - git_diff:    `git diff --stat <changed_files>` 输出 ≥ 1 行变更
  - build:       file-scoped `npm run build` / `pytest --collect-only`
  - test:        file-scoped test run

**Toolchain 缺失处理**（用户机现实，PRD §3 D7 末段）：
每个 verifier 的 prepare() 阶段先 `which` 检测前置工具；缺则
status=skipped, reason="missing_X"，**不阻 end_turn**。

**Verifier 自身硬超时 60s**：超时 → status=timeout，不阻 end_turn（防自杀）。

**Scope 隔离**（PRD D7 + I3-4）：build/test 必须用 changed_files 限定 scope，
禁止全仓库扫（节约资源 + 避免触碰用户其他正在改的代码）。
"""
from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal, Optional

from deskpet.tools.receipt import ToolReceipt

logger = logging.getLogger(__name__)

VERIFIER_TIMEOUT_S: float = 60.0
LOG_TAIL_LINES: int = 20


@dataclass
class VerifierOutcome:
    """单个 verifier 的结果（PRD §3.1 IDL）。"""
    verifier: str
    status: Literal["pass", "fail", "skipped", "timeout"]
    reason: str = ""
    log_tail: Optional[str] = None
    error_class: Optional[str] = None
    elapsed_ms: int = 0


# ─── Helpers ─────────────────────────────────────────────────

def _which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def _tail_lines(text: str, n: int = LOG_TAIL_LINES) -> str:
    lines = text.strip().split("\n")
    if len(lines) <= n:
        return text.strip()
    return "\n".join(lines[-n:])


def _sha256_file(path: Path) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


# ─── 1. file_exists verifier ─────────────────────────────────

class FileExistsVerifier:
    """T10-1/T10-2/T10-3: 验 receipt 声称的 path 真实存在 + size>0 + sha256 匹配。"""
    NAME = "file_exists"

    def verify(
        self,
        *,
        receipts: Iterable[ToolReceipt],
        expected_paths_and_shas: Iterable[tuple[str, Optional[str]]] = (),
    ) -> VerifierOutcome:
        """expected_paths_and_shas: list of (path, expected_sha256 or None).
        sha=None 表示 receipt 处于 sha256_pending — 仅验存在 + size。
        """
        # 不依赖任何 toolchain
        missing: list[str] = []
        size_zero: list[str] = []
        sha_mismatch: list[str] = []
        for path_str, expected_sha in expected_paths_and_shas:
            p = Path(path_str)
            if not p.exists():
                missing.append(path_str)
                continue
            try:
                if p.stat().st_size == 0:
                    size_zero.append(path_str)
                    continue
            except OSError:
                missing.append(path_str)
                continue
            if expected_sha:
                actual = _sha256_file(p)
                if actual is None or actual != expected_sha:
                    sha_mismatch.append(path_str)
        # 决策
        if not missing and not size_zero and not sha_mismatch:
            return VerifierOutcome(verifier=self.NAME, status="pass")
        reasons = []
        if missing:
            reasons.append(f"missing={missing}")
        if size_zero:
            reasons.append(f"size_zero={size_zero}")
        if sha_mismatch:
            reasons.append(f"sha256_mismatch={sha_mismatch}")
        return VerifierOutcome(
            verifier=self.NAME,
            status="fail",
            reason="; ".join(reasons),
            error_class="missing_file",
        )


# ─── 2. git_diff verifier ────────────────────────────────────

class GitDiffVerifier:
    """T10-4: 调过 file_write/patch 类工具 → git diff --stat 应有变更。"""
    NAME = "git_diff"

    def verify(
        self,
        *,
        changed_files: list[str],
        cwd: str,
    ) -> VerifierOutcome:
        if _which("git") is None:
            return VerifierOutcome(
                verifier=self.NAME, status="skipped", reason="missing_git",
            )
        if not (Path(cwd) / ".git").exists():
            # also accept worktree (gitdir file)
            git_path = Path(cwd) / ".git"
            if not (git_path.exists() or
                    (Path(cwd).resolve() / ".git").exists()):
                return VerifierOutcome(
                    verifier=self.NAME, status="skipped", reason="not_a_git_repo",
                )
        try:
            args = ["git", "diff", "--stat"]
            if changed_files:
                args.extend(["--"] + changed_files)
            result = subprocess.run(
                args, cwd=cwd, capture_output=True, text=True,
                timeout=VERIFIER_TIMEOUT_S, encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            return VerifierOutcome(
                verifier=self.NAME, status="timeout",
                reason=f"git diff exceeded {VERIFIER_TIMEOUT_S}s",
            )
        except OSError as exc:
            return VerifierOutcome(
                verifier=self.NAME, status="skipped",
                reason=f"git_exec_failed: {exc}",
            )
        out = (result.stdout or "").strip()
        # `git diff --stat` 输出空 = 无变更
        if not out:
            return VerifierOutcome(
                verifier=self.NAME, status="fail",
                reason="no_changes_detected", error_class="missing_file",
                log_tail=out,
            )
        return VerifierOutcome(verifier=self.NAME, status="pass", log_tail=out[:500])


# ─── 3. build verifier (frontend / backend) ──────────────────

class BuildVerifier:
    """T10-5: file-scoped build；缺 toolchain skip；失败回灌末 20 行。"""
    NAME = "build"

    def __init__(self, *, kind: Literal["frontend", "backend"] = "backend") -> None:
        self.kind = kind

    def verify(
        self,
        *,
        changed_files: list[str],
        cwd: str,
    ) -> VerifierOutcome:
        if self.kind == "frontend":
            return self._verify_frontend(changed_files, cwd)
        return self._verify_backend(changed_files, cwd)

    def _verify_frontend(self, changed_files: list[str], cwd: str) -> VerifierOutcome:
        # T10-8: which npm 缺则 skip
        if _which("npx") is None and _which("npm") is None:
            return VerifierOutcome(
                verifier=self.NAME, status="skipped", reason="missing_npm",
            )
        if not (Path(cwd) / "node_modules").exists():
            return VerifierOutcome(
                verifier=self.NAME, status="skipped", reason="missing_node_modules",
            )
        ts_files = [f for f in changed_files if f.endswith((".ts", ".tsx"))]
        if not ts_files:
            return VerifierOutcome(
                verifier=self.NAME, status="skipped",
                reason="no_typescript_files_changed",
            )
        try:
            result = subprocess.run(
                ["npx", "tsc", "--noEmit", *ts_files],
                cwd=cwd, capture_output=True, text=True,
                timeout=VERIFIER_TIMEOUT_S, encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            return VerifierOutcome(
                verifier=self.NAME, status="timeout",
                reason=f"tsc exceeded {VERIFIER_TIMEOUT_S}s",
            )
        if result.returncode == 0:
            return VerifierOutcome(verifier=self.NAME, status="pass")
        tail = _tail_lines((result.stdout or "") + (result.stderr or ""))
        return VerifierOutcome(
            verifier=self.NAME, status="fail",
            reason="tsc_failed", error_class="build_error", log_tail=tail,
        )

    def _verify_backend(self, changed_files: list[str], cwd: str) -> VerifierOutcome:
        if _which("python") is None:
            return VerifierOutcome(
                verifier=self.NAME, status="skipped", reason="missing_python",
            )
        py_files = [f for f in changed_files if f.endswith(".py")]
        if not py_files:
            return VerifierOutcome(
                verifier=self.NAME, status="skipped",
                reason="no_python_files_changed",
            )
        try:
            result = subprocess.run(
                ["python", "-m", "py_compile", *py_files],
                cwd=cwd, capture_output=True, text=True,
                timeout=VERIFIER_TIMEOUT_S, encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            return VerifierOutcome(
                verifier=self.NAME, status="timeout",
                reason=f"py_compile exceeded {VERIFIER_TIMEOUT_S}s",
            )
        if result.returncode == 0:
            return VerifierOutcome(verifier=self.NAME, status="pass")
        tail = _tail_lines((result.stdout or "") + (result.stderr or ""))
        return VerifierOutcome(
            verifier=self.NAME, status="fail",
            reason="py_compile_failed", error_class="build_error", log_tail=tail,
        )


# ─── 4. test verifier ────────────────────────────────────────

class TestVerifier:
    """T10-6: file-scoped pytest；缺 toolchain skip；失败回灌末 20 行。"""
    NAME = "test"

    def verify(
        self,
        *,
        changed_files: list[str],
        cwd: str,
    ) -> VerifierOutcome:
        # T10-8: which pytest
        if _which("pytest") is None:
            return VerifierOutcome(
                verifier=self.NAME, status="skipped", reason="missing_pytest",
            )
        test_files = [f for f in changed_files
                      if f.endswith(".py") and ("test_" in f or f.endswith("_test.py"))]
        if not test_files:
            return VerifierOutcome(
                verifier=self.NAME, status="skipped",
                reason="no_test_files_changed",
            )
        try:
            result = subprocess.run(
                ["pytest", "-q", "--tb=short", *test_files],
                cwd=cwd, capture_output=True, text=True,
                timeout=VERIFIER_TIMEOUT_S, encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            return VerifierOutcome(
                verifier=self.NAME, status="timeout",
                reason=f"pytest exceeded {VERIFIER_TIMEOUT_S}s",
            )
        if result.returncode == 0:
            return VerifierOutcome(verifier=self.NAME, status="pass")
        tail = _tail_lines((result.stdout or "") + (result.stderr or ""))
        return VerifierOutcome(
            verifier=self.NAME, status="fail",
            reason="pytest_failed", error_class="test_error", log_tail=tail,
        )


# ─── Entry point ─────────────────────────────────────────────

@dataclass
class OutcomeReport:
    """Aggregate outcome of running multiple verifiers."""
    outcomes: list[VerifierOutcome] = field(default_factory=list)

    def has_failures(self) -> bool:
        return any(o.status == "fail" for o in self.outcomes)

    def to_feedback_message(self) -> str:
        """D8 schema 回灌 system message。"""
        lines = ["[verify-gate] outcome verifiers report:"]
        for i, o in enumerate(self.outcomes, 1):
            head = f"  {i}. [{o.error_class or o.status}] {o.verifier} — {o.reason}"
            lines.append(head)
            if o.log_tail and o.status == "fail":
                lines.append("     last 20 lines:")
                for ln in o.log_tail.split("\n"):
                    lines.append(f"     {ln}")
        return "\n".join(lines)


def run_outcome_verifiers(
    *,
    receipts: list[ToolReceipt],
    changed_files: list[str],
    cwd: str,
    expected_paths_and_shas: Optional[list[tuple[str, Optional[str]]]] = None,
    run_build: bool = False,
    run_tests: bool = False,
    build_kind: Literal["frontend", "backend"] = "backend",
) -> OutcomeReport:
    """Run the 4-verifier suite based on what receipts indicate.

    PRD §3 D7 触发条件：
      - file_exists 始终跑（任何 artifact.kind=file）
      - git_diff 在调过 file_write/patch 类工具时
      - build/test 在 changed_files 涉及代码改动 + flag on 时
    """
    report = OutcomeReport()
    # 1. file_exists
    if expected_paths_and_shas:
        report.outcomes.append(
            FileExistsVerifier().verify(
                receipts=receipts,
                expected_paths_and_shas=expected_paths_and_shas,
            )
        )
    # 2. git_diff — 仅当有 changed_files 时（callers 应只在文件写工具
    # 出现后传入 changed_files）
    if changed_files:
        report.outcomes.append(
            GitDiffVerifier().verify(changed_files=changed_files, cwd=cwd)
        )
    # 3. build
    if run_build and changed_files:
        report.outcomes.append(
            BuildVerifier(kind=build_kind).verify(
                changed_files=changed_files, cwd=cwd
            )
        )
    # 4. test
    if run_tests and changed_files:
        report.outcomes.append(
            TestVerifier().verify(changed_files=changed_files, cwd=cwd)
        )
    return report


__all__ = [
    "VerifierOutcome",
    "OutcomeReport",
    "FileExistsVerifier",
    "GitDiffVerifier",
    "BuildVerifier",
    "TestVerifier",
    "run_outcome_verifiers",
    "VERIFIER_TIMEOUT_S",
]
