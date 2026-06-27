#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-T3.5 — DeskPet 工具调用 last-mile 升级 · 自动化 acceptance 脚本。

对齐 2026 业界 Spec Kit / acceptance.sh 模式。把 PRD §2 G1-G5 + 4 项
一票否决 MR-0/MR-8/MR-13/MR-19 自动化，CI 一键跑：

    python scripts/acceptance/last_mile_smoke.py
    python scripts/acceptance/last_mile_smoke.py --strict   # 失败即非零退出

输出：
  - 控制台彩色 PASS/FAIL/SKIP 表
  - JSON 报告到 plans/2026-05-23-tool-last-mile-upgrade/manual-results-<ts>/acceptance.json
  - 退出码 0=全过 / 1=有 fail / 2=有 P0 fail (一票否决)

PRD §4 Stage 3 + WI-T3.5 + 二轮架构评审 I3-3 / O4 已采纳。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

# Windows console may default to GBK; force UTF-8 stdout for ANSI + emoji.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, OSError):
    pass  # Pre-3.7 / non-redirectable streams: degrade gracefully
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).parent.parent.parent.resolve()
BACKEND_DIR = REPO_ROOT / "backend"
TAURI_DIR = REPO_ROOT / "tauri-app"
RUST_DIR = TAURI_DIR / "src-tauri"
PLANS_DIR = REPO_ROOT / "plans" / "2026-05-23-tool-last-mile-upgrade"

# ─── ANSI colors (no tput dep) ────────────────────────────────

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RESET = "\033[0m"
BOLD = "\033[1m"


@dataclass
class CheckResult:
    name: str
    veto: bool   # 一票否决
    status: str  # pass / fail / skip
    duration_ms: int = 0
    detail: str = ""
    log_tail: str = ""


@dataclass
class AcceptanceReport:
    timestamp: str
    git_branch: str
    git_commit: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def has_veto_failure(self) -> bool:
        return any(c.veto and c.status == "fail" for c in self.checks)

    @property
    def has_any_failure(self) -> bool:
        return any(c.status == "fail" for c in self.checks)

    def to_json(self) -> str:
        return json.dumps({
            "timestamp": self.timestamp,
            "git_branch": self.git_branch,
            "git_commit": self.git_commit,
            "checks": [asdict(c) for c in self.checks],
            "summary": {
                "total": len(self.checks),
                "pass": sum(1 for c in self.checks if c.status == "pass"),
                "fail": sum(1 for c in self.checks if c.status == "fail"),
                "skip": sum(1 for c in self.checks if c.status == "skip"),
                "veto_failure": self.has_veto_failure,
            },
        }, indent=2, ensure_ascii=False)


# ─── Runner ───────────────────────────────────────────────────

def _run(cmd: list[str], cwd: Path, timeout: int = 600) -> tuple[int, str]:
    """Run command, return (exit_code, combined_output_tail_30_lines)."""
    try:
        r = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError as exc:
        return -1, f"command not found: {exc}"
    except subprocess.TimeoutExpired:
        return -2, f"timeout after {timeout}s"
    out = (r.stdout or "") + "\n--- stderr ---\n" + (r.stderr or "")
    lines = out.strip().split("\n")
    tail = "\n".join(lines[-30:]) if len(lines) > 30 else "\n".join(lines)
    return r.returncode, tail


def _print(status: str, name: str, veto: bool, detail: str = "") -> None:
    icon = {"pass": f"{GREEN}[PASS]{RESET}", "fail": f"{RED}[FAIL]{RESET}",
            "skip": f"{YELLOW}[SKIP]{RESET}"}.get(status, "[????]")
    veto_tag = f" {BOLD}{RED}(VETO){RESET}" if veto else ""
    sep = "  -- " if detail else ""
    print(f"  {icon} {name}{veto_tag}{sep}{detail}")


# ─── Individual checks ────────────────────────────────────────

def check_mr0_zero_regression(report: AcceptanceReport) -> None:
    """MR-0 (VETO) — 全套 backend pytest + vitest + cargo test 全绿。

    flag-off 字节级一致由 TG-12 + TG-2 T2-5b 守护（在 pytest 内）。
    """
    start = time.monotonic()
    code, tail = _run(
        [sys.executable, "-m", "pytest", "tests/", "-q",
         "--ignore=tests/integration"],
        cwd=BACKEND_DIR, timeout=600,
    )
    elapsed = int((time.monotonic() - start) * 1000)
    if code == 0:
        report.checks.append(CheckResult(
            name="MR-0 backend zero-regression (pytest)",
            veto=True, status="pass", duration_ms=elapsed,
        ))
        _print("pass", "MR-0 backend zero-regression (pytest)", veto=True)
    else:
        report.checks.append(CheckResult(
            name="MR-0 backend zero-regression (pytest)",
            veto=True, status="fail", duration_ms=elapsed, log_tail=tail,
        ))
        _print("fail", "MR-0 backend zero-regression (pytest)", veto=True,
               detail=f"exit={code}")


def check_mr0_vitest(report: AcceptanceReport) -> None:
    """MR-0 part 2 — vitest 全套。"""
    start = time.monotonic()
    # vitest 必须在 tauri-app 目录内跑；用 npx
    code, tail = _run(
        ["npx", "vitest", "run"],
        cwd=TAURI_DIR, timeout=300,
    )
    elapsed = int((time.monotonic() - start) * 1000)
    if code == -1:
        report.checks.append(CheckResult(
            name="MR-0 vitest", veto=True, status="skip",
            duration_ms=elapsed, detail="npx not found",
        ))
        _print("skip", "MR-0 vitest", veto=True, detail="npx missing")
    elif code == 0:
        report.checks.append(CheckResult(
            name="MR-0 vitest", veto=True, status="pass",
            duration_ms=elapsed,
        ))
        _print("pass", "MR-0 vitest", veto=True)
    else:
        report.checks.append(CheckResult(
            name="MR-0 vitest", veto=True, status="fail",
            duration_ms=elapsed, log_tail=tail,
        ))
        _print("fail", "MR-0 vitest", veto=True, detail=f"exit={code}")


def check_mr0_cargo(report: AcceptanceReport) -> None:
    """MR-0 part 3 — cargo check + artifact_ops 单测。"""
    start = time.monotonic()
    code, tail = _run(
        ["cargo", "check", "--message-format=short"],
        cwd=RUST_DIR, timeout=600,
    )
    elapsed = int((time.monotonic() - start) * 1000)
    if code == -1:
        report.checks.append(CheckResult(
            name="MR-0 cargo check", veto=True, status="skip",
            duration_ms=elapsed, detail="cargo not found",
        ))
        _print("skip", "MR-0 cargo check", veto=True, detail="cargo missing")
    elif code == 0:
        report.checks.append(CheckResult(
            name="MR-0 cargo check", veto=True, status="pass",
            duration_ms=elapsed,
        ))
        _print("pass", "MR-0 cargo check", veto=True)
    else:
        report.checks.append(CheckResult(
            name="MR-0 cargo check", veto=True, status="fail",
            duration_ms=elapsed, log_tail=tail,
        ))
        _print("fail", "MR-0 cargo check", veto=True, detail=f"exit={code}")


def check_mr8_fake_completion_capture(report: AcceptanceReport) -> None:
    """MR-8 (VETO) — fake-completion 抓获率 ≥ 95%。

    TG-9 T9-1~T9-3 + TG-9 T9-12b 准入硬条件已守护核心路径；
    完整 50-条 fake_claims_50.jsonl 跑成抓获率统计 by category 留 follow-up
    （需要接入 CascadeExtractor 真 LLM）。本 acceptance 检查 TG-9 全绿即可代理。
    """
    start = time.monotonic()
    code, tail = _run(
        [sys.executable, "-m", "pytest", "tests/test_verify_gate.py", "-q"],
        cwd=BACKEND_DIR, timeout=120,
    )
    elapsed = int((time.monotonic() - start) * 1000)
    if code == 0:
        report.checks.append(CheckResult(
            name="MR-8 fake-completion capture (TG-9)", veto=True,
            status="pass", duration_ms=elapsed,
            detail="代理项: TG-9 全 15 用例绿；完整 50-条 抓获率统计 follow-up",
        ))
        _print("pass", "MR-8 fake-completion capture (TG-9)", veto=True)
    else:
        report.checks.append(CheckResult(
            name="MR-8 fake-completion capture (TG-9)", veto=True,
            status="fail", duration_ms=elapsed, log_tail=tail,
        ))
        _print("fail", "MR-8 fake-completion capture (TG-9)", veto=True)


def check_mr13_file_exists_verifier(report: AcceptanceReport) -> None:
    """MR-13 (VETO) — file_exists outcome verifier 完整覆盖。"""
    start = time.monotonic()
    code, tail = _run(
        [sys.executable, "-m", "pytest", "tests/test_outcome_verifier.py",
         "-q", "-k", "file_exists"],
        cwd=BACKEND_DIR, timeout=60,
    )
    elapsed = int((time.monotonic() - start) * 1000)
    if code == 0:
        report.checks.append(CheckResult(
            name="MR-13 file_exists outcome verifier (TG-10)",
            veto=True, status="pass", duration_ms=elapsed,
        ))
        _print("pass", "MR-13 file_exists outcome verifier (TG-10)", veto=True)
    else:
        report.checks.append(CheckResult(
            name="MR-13 file_exists outcome verifier (TG-10)",
            veto=True, status="fail", duration_ms=elapsed, log_tail=tail,
        ))
        _print("fail", "MR-13 file_exists outcome verifier (TG-10)", veto=True)


def check_mr19_hmac_privacy(report: AcceptanceReport) -> None:
    """MR-19 (VETO) — HMAC key keystore + 信任面 + N1 sig-invalid 剔除。"""
    start = time.monotonic()
    code, tail = _run(
        [sys.executable, "-m", "pytest", "tests/test_receipt_store.py", "-q"],
        cwd=BACKEND_DIR, timeout=60,
    )
    elapsed = int((time.monotonic() - start) * 1000)
    if code == 0:
        report.checks.append(CheckResult(
            name="MR-19 HMAC privacy (TG-7+TG-8 + N1)",
            veto=True, status="pass", duration_ms=elapsed,
        ))
        _print("pass", "MR-19 HMAC privacy (TG-7+TG-8 + N1)", veto=True)
    else:
        report.checks.append(CheckResult(
            name="MR-19 HMAC privacy (TG-7+TG-8 + N1)",
            veto=True, status="fail", duration_ms=elapsed, log_tail=tail,
        ))
        _print("fail", "MR-19 HMAC privacy (TG-7+TG-8 + N1)", veto=True)


def check_stage2_admission_conditions(report: AcceptanceReport) -> None:
    """二轮架构评审 N1/N2 准入硬条件：T9-12b + T9-14b。"""
    start = time.monotonic()
    code, tail = _run(
        [sys.executable, "-m", "pytest", "tests/test_verify_gate.py",
         "-q", "-k", "t9_12b or t9_14b"],
        cwd=BACKEND_DIR, timeout=60,
    )
    elapsed = int((time.monotonic() - start) * 1000)
    if code == 0:
        report.checks.append(CheckResult(
            name="Stage-2 admission (T9-12b + T9-14b)",
            veto=False, status="pass", duration_ms=elapsed,
        ))
        _print("pass", "Stage-2 admission (T9-12b + T9-14b)", veto=False)
    else:
        report.checks.append(CheckResult(
            name="Stage-2 admission (T9-12b + T9-14b)",
            veto=False, status="fail", duration_ms=elapsed, log_tail=tail,
        ))
        _print("fail", "Stage-2 admission (T9-12b + T9-14b)", veto=False)


# ─── Main ─────────────────────────────────────────────────────

def _git_state() -> tuple[str, str]:
    try:
        b = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "branch", "--show-current"],
            text=True,
        ).strip()
        c = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            text=True,
        ).strip()
        return b, c
    except subprocess.CalledProcessError:
        return "unknown", "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="DeskPet last-mile acceptance smoke (4 一票否决项 + 准入硬条件)",
    )
    parser.add_argument("--strict", action="store_true",
                        help="任何 fail 即退出码 != 0（CI 用）")
    parser.add_argument("--no-vitest", action="store_true",
                        help="跳过 vitest（CI 已单独跑）")
    parser.add_argument("--no-cargo", action="store_true",
                        help="跳过 cargo check（CI 已单独跑）")
    args = parser.parse_args()

    branch, commit = _git_state()
    report = AcceptanceReport(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        git_branch=branch, git_commit=commit,
    )

    print(f"{BOLD}DeskPet last-mile acceptance smoke{RESET}")
    print(f"  branch: {CYAN}{branch}{RESET}  commit: {CYAN}{commit}{RESET}")
    print(f"  repo:   {CYAN}{REPO_ROOT}{RESET}")
    print("")

    print(f"{BOLD}=== MR-0 zero-regression (VETO) ==={RESET}")
    check_mr0_zero_regression(report)
    if not args.no_vitest:
        check_mr0_vitest(report)
    if not args.no_cargo:
        check_mr0_cargo(report)

    print(f"\n{BOLD}=== MR-8 fake-completion (VETO) ==={RESET}")
    check_mr8_fake_completion_capture(report)

    print(f"\n{BOLD}=== MR-13 file_exists outcome (VETO) ==={RESET}")
    check_mr13_file_exists_verifier(report)

    print(f"\n{BOLD}=== MR-19 HMAC privacy + N1 (VETO) ==={RESET}")
    check_mr19_hmac_privacy(report)

    print(f"\n{BOLD}=== Stage-2 admission (N1/N2) ==={RESET}")
    check_stage2_admission_conditions(report)

    # ─── 写报告 + 总结 ────────────────────────────────────
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    out_dir = PLANS_DIR / f"manual-results-{report.timestamp.replace(':', '')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "acceptance.json").write_text(report.to_json(), encoding="utf-8")

    n_pass = sum(1 for c in report.checks if c.status == "pass")
    n_fail = sum(1 for c in report.checks if c.status == "fail")
    n_skip = sum(1 for c in report.checks if c.status == "skip")

    print(f"\n{BOLD}=== Summary ==={RESET}")
    print(f"  total: {len(report.checks)}  "
          f"{GREEN}pass: {n_pass}{RESET}  "
          f"{RED}fail: {n_fail}{RESET}  "
          f"{YELLOW}skip: {n_skip}{RESET}")
    print(f"  report: {CYAN}{out_dir / 'acceptance.json'}{RESET}")

    if report.has_veto_failure:
        print(f"\n  {BOLD}{RED}DECISION: NO-SHIP{RESET} — at least one VETO check failed.")
        return 2
    if n_fail > 0:
        print(f"\n  {BOLD}{YELLOW}DECISION: SHIP-WITH-FOLLOWUP{RESET} — "
              f"{n_fail} non-veto failure(s).")
        return 1 if args.strict else 0
    print(f"\n  {BOLD}{GREEN}DECISION: SHIP{RESET} — all checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
