# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""TG-S4 — eval 门控严格化测试（WI-S2.3 / PRD D10, D11）。

10 个用例覆盖：
* TS4-1~4：``--strict`` 与默认 gate 的判定差异
* TS4-5~8：``--update-baseline`` 的 sanity + ``--force`` 绕过
* TS4-9~10：``eval_gate_ci.sh`` git diff 自动 strict 触发

Mock 策略：用 monkeypatch 替换 ``scripts.eval_gate.run_eval`` 返回固定 dict，
避免每个 case 都跑真 MetricsRunner。baseline 路径用 ``tmp_path`` +
monkeypatch ``_BASELINE_PATH``。

``eval_gate_ci.sh`` 在 Windows 下用 git-bash（msys2）跑得动，本机
``which bash`` 已确认；CI 是 ubuntu-latest 直接原生。任何环境拿不到
bash 时用 ``pytest.skip`` 跳。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# 让测试能 import scripts.eval_gate（scripts 不是包，但 _BASELINE_PATH
# 模式跟生产 entrypoint 一致用 sys.path）
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))
sys.path.insert(0, str(_BACKEND_ROOT / "scripts"))

import eval_gate  # noqa: E402


# ---------------- 公共 fixtures -----------------------------------------

@pytest.fixture
def tmp_baseline(tmp_path, monkeypatch):
    """把 _BASELINE_PATH 重定向到 tmp，避免污染仓库 baseline。"""
    p = tmp_path / "zh_baseline.json"
    monkeypatch.setattr(eval_gate, "_BASELINE_PATH", p)
    return p


def _stub_run_eval(monkeypatch, result: dict) -> None:
    """让 eval_gate.run_eval 直接返 result（不真跑 MetricsRunner）。"""

    async def _fake(*, top_k: int = 20):  # noqa: ARG001
        return dict(result)

    monkeypatch.setattr(eval_gate, "run_eval", _fake)


def _run_main(argv: list[str]) -> int:
    """模拟命令行调 eval_gate.main()。"""
    old = sys.argv
    sys.argv = ["eval_gate", *argv]
    try:
        return eval_gate.main()
    finally:
        sys.argv = old


# ============== TS4-1: --strict baseline = current → fail ===============

def test_ts4_1_strict_baseline_equal_current_fails(tmp_baseline, monkeypatch):
    baseline = {
        "qa_set_size": 35, "hit@1": 0.34, "hit@5": 0.4286,
        "hit@10": 0.83, "mrr": 0.42, "token_per_query": 195.0,
    }
    tmp_baseline.write_text(json.dumps(baseline), encoding="utf-8")
    # 当前 == baseline → 没有"严格提升"
    _stub_run_eval(monkeypatch, baseline)

    rc = _run_main(["--strict"])
    assert rc != 0, "strict 模式下 hit@5 == baseline 必须 FAIL"


# ============== TS4-2: --strict hit@5 显著提升 → exit 0 =================

def test_ts4_2_strict_significant_improvement_passes(tmp_baseline, monkeypatch):
    baseline = {
        "qa_set_size": 35, "hit@5": 0.40, "token_per_query": 200.0,
    }
    tmp_baseline.write_text(json.dumps(baseline), encoding="utf-8")
    # cur 比 baseline+_HIT_TOLERANCE(0.02) 还高 0.01 → 严格 > → 通过
    current = {"qa_set_size": 35, "hit@5": 0.43, "token_per_query": 210.0}
    _stub_run_eval(monkeypatch, current)

    rc = _run_main(["--strict"])
    assert rc == 0


# ============== TS4-3: --strict token 超 +30% → fail ===================

def test_ts4_3_strict_token_overshoot_fails(tmp_baseline, monkeypatch):
    baseline = {
        "qa_set_size": 35, "hit@5": 0.40, "token_per_query": 200.0,
    }
    tmp_baseline.write_text(json.dumps(baseline), encoding="utf-8")
    # hit@5 提升但 token 超 30%
    current = {"qa_set_size": 35, "hit@5": 0.50, "token_per_query": 280.0}
    _stub_run_eval(monkeypatch, current)

    rc = _run_main(["--strict"])
    assert rc != 0


# ============== TS4-4: 默认 gate baseline = current → exit 0 ============

def test_ts4_4_default_gate_baseline_equal_current_passes(
    tmp_baseline, monkeypatch,
):
    baseline = {
        "qa_set_size": 35, "hit@5": 0.4286, "token_per_query": 195.0,
    }
    tmp_baseline.write_text(json.dumps(baseline), encoding="utf-8")
    _stub_run_eval(monkeypatch, baseline)

    rc = _run_main([])  # 默认 gate
    assert rc == 0, "默认 gate 是'不回归'(≤)，持平当然算通过"


# ============== TS4-5: --update-baseline 钉低 hit@5 → exit 3 ===========

def test_ts4_5_update_baseline_lower_hit_rejected(tmp_baseline, monkeypatch):
    old = {
        "qa_set_size": 35, "hit@5": 0.50, "token_per_query": 200.0,
    }
    tmp_baseline.write_text(json.dumps(old), encoding="utf-8")
    # 新结果 hit@5 比 old - 容差 还低
    current = {"qa_set_size": 35, "hit@5": 0.40, "token_per_query": 200.0}
    _stub_run_eval(monkeypatch, current)

    rc = _run_main(["--update-baseline"])
    assert rc == 3, "钉低 baseline 必须返 exit 3"
    # baseline 文件未被覆盖
    on_disk = json.loads(tmp_baseline.read_text(encoding="utf-8"))
    assert on_disk["hit@5"] == 0.50


# ============== TS4-6: --update-baseline --force 强写 → exit 0 ==========

def test_ts4_6_update_baseline_force_overrides(tmp_baseline, monkeypatch):
    old = {
        "qa_set_size": 35, "hit@5": 0.50, "token_per_query": 200.0,
    }
    tmp_baseline.write_text(json.dumps(old), encoding="utf-8")
    current = {"qa_set_size": 35, "hit@5": 0.40, "token_per_query": 200.0}
    _stub_run_eval(monkeypatch, current)

    rc = _run_main(["--update-baseline", "--force"])
    assert rc == 0
    on_disk = json.loads(tmp_baseline.read_text(encoding="utf-8"))
    assert on_disk["hit@5"] == 0.40, "--force 应覆盖低 hit@5 baseline"


# ============== TS4-7: --update-baseline token 钉高 → exit 3 ===========

def test_ts4_7_update_baseline_higher_token_rejected(tmp_baseline, monkeypatch):
    old = {
        "qa_set_size": 35, "hit@5": 0.45, "token_per_query": 200.0,
    }
    tmp_baseline.write_text(json.dumps(old), encoding="utf-8")
    # hit@5 ok 但 token 是旧值 ×1.5（超 1.30 上限）
    current = {"qa_set_size": 35, "hit@5": 0.46, "token_per_query": 300.0}
    _stub_run_eval(monkeypatch, current)

    rc = _run_main(["--update-baseline"])
    assert rc == 3


# ============== TS4-8: 首次 update（无 old baseline）→ exit 0 ============

def test_ts4_8_update_baseline_first_time_passes(tmp_baseline, monkeypatch):
    assert not tmp_baseline.exists()
    current = {
        "qa_set_size": 35, "hit@1": 0.30, "hit@5": 0.40,
        "hit@10": 0.80, "mrr": 0.40, "token_per_query": 200.0,
    }
    _stub_run_eval(monkeypatch, current)

    rc = _run_main(["--update-baseline"])
    assert rc == 0
    assert tmp_baseline.exists()
    on_disk = json.loads(tmp_baseline.read_text(encoding="utf-8"))
    assert on_disk["hit@5"] == 0.40


# ============== sanity helper 单元测试（辅助 TS4-5/6/7/8）===============

def test_check_update_sanity_helper_paths():
    """直接测 _check_update_sanity 的 4 个分支。"""
    old = {"hit@5": 0.50, "token_per_query": 200.0}

    # 1) 首次写（old=None）→ ok
    ok, _ = eval_gate._check_update_sanity({"hit@5": 0.1}, None, force=False)
    assert ok

    # 2) force=True → ok（即使指标坍塌）
    ok, _ = eval_gate._check_update_sanity(
        {"hit@5": 0.0, "token_per_query": 999.0}, old, force=True,
    )
    assert ok

    # 3) hit@5 坍塌 → 拒绝
    ok, reason = eval_gate._check_update_sanity(
        {"hit@5": 0.30, "token_per_query": 200.0}, old, force=False,
    )
    assert not ok and "hit@5" in reason

    # 4) token 超标 → 拒绝
    ok, reason = eval_gate._check_update_sanity(
        {"hit@5": 0.55, "token_per_query": 300.0}, old, force=False,
    )
    assert not ok and "token" in reason


# ============== TS4-9 / TS4-10: eval_gate_ci.sh git diff 触发 ==========

_CI_SH = _BACKEND_ROOT / "scripts" / "eval_gate_ci.sh"
_BASH = shutil.which("bash")


def _make_repo_with_diff(tmp_path: Path, changed_files: list[str]) -> Path:
    """造一个临时 git repo，初始 commit 一份空骨架，再 commit 改动文件，
    使 ``git diff --name-only HEAD~1 HEAD`` 返回 ``changed_files``。

    返回 repo 根目录。脚本被复制进去 + 改成 stub（让 python -m
    scripts.eval_gate 直接 echo 收到的参数，不真跑 eval）。
    """
    repo = tmp_path / "repo"
    (repo / "backend" / "scripts").mkdir(parents=True)
    # 初始 commit：空 README
    (repo / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(
        ["git", "init", "-q", "-b", "main"], cwd=repo, check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test"], cwd=repo, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"], cwd=repo, check=True,
    )
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True,
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"], cwd=repo, check=True,
    )

    # 把真 ci.sh 复制过去
    ci_dst = repo / "backend" / "scripts" / "eval_gate_ci.sh"
    ci_dst.write_bytes(_CI_SH.read_bytes())
    ci_dst.chmod(0o755)
    # 造 scripts/eval_gate.py stub —— 只 echo 命令行 args，不跑真 eval
    eval_stub = repo / "backend" / "scripts" / "eval_gate.py"
    eval_stub.write_text(
        "import sys\nprint('[stub] argv=' + ' '.join(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    # 触发 import scripts.eval_gate 也得能跑 → 给 scripts 包加 __init__
    (repo / "backend" / "scripts" / "__init__.py").write_text(
        "", encoding="utf-8",
    )

    # 第二次 commit：按入参写 changed_files（在 README 同行 append 也算改）
    for rel in changed_files:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("change\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "change"], cwd=repo, check=True,
    )
    return repo


@pytest.mark.skipif(_BASH is None, reason="bash 不可用（Windows 无 git-bash）")
def test_ts4_9_ci_sh_triggers_strict_on_retriever_change(tmp_path):
    repo = _make_repo_with_diff(
        tmp_path, ["backend/deskpet/memory/enhanced_retriever.py"],
    )
    # ci.sh 默认 BASE_REF=origin/master 拉不到 → 自动 fallback 到 HEAD~1..HEAD
    env = dict(os.environ)
    env.pop("BASE_REF", None)
    out = subprocess.run(
        [_BASH, "backend/scripts/eval_gate_ci.sh"],
        cwd=repo, env=env, capture_output=True,
        encoding="utf-8", errors="replace",
    )
    combined = (out.stdout or "") + "\n" + (out.stderr or "")
    assert "召回相关改动检测" in combined, combined
    assert "--strict" in combined, combined
    assert "enhanced_retriever.py" in combined, combined
    # stub 收到 --strict
    assert "[stub] argv=--strict" in combined, combined


@pytest.mark.skipif(_BASH is None, reason="bash 不可用（Windows 无 git-bash）")
def test_ts4_10_ci_sh_skips_strict_on_unrelated_change(tmp_path):
    repo = _make_repo_with_diff(tmp_path, ["docs/CHANGELOG.md"])
    env = dict(os.environ)
    env.pop("BASE_REF", None)
    out = subprocess.run(
        [_BASH, "backend/scripts/eval_gate_ci.sh"],
        cwd=repo, env=env, capture_output=True,
        encoding="utf-8", errors="replace",
    )
    combined = (out.stdout or "") + "\n" + (out.stderr or "")
    assert "未检测到召回相关改动" in combined, combined
    assert "--strict" not in combined, combined
    # stub 收到空 argv（默认 gate）
    assert "[stub] argv=" in combined, combined
