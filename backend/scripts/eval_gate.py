# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""记忆系统升级 WI-M0.2 / D8 — eval 指标 pre-merge 门控脚本。

memory-v2 上一轮变死代码的同类失误就是"靠人工记得跑"。本脚本把 eval
门控自动化：seed 中文回测集 fixture → 跑 ``MetricsRunner`` → 与钉死的
baseline 比对 → 指标回归则**非零退出**，可挂进 pre-merge / CI。

判定口径
--------
* ``hit@5`` 不得回归：新值 ≥ baseline − ``_HIT_TOLERANCE``。
* ``token_per_query`` 增幅 ≤ +30%（PRD §3 D11 / 成功度量）。

为了**可复现**，本脚本固定用 mock embedder（hash 向量，确定性）+ 钉死的
中文 fixture —— 任何机器跑都得到一致结果，不依赖 BGE-M3 权重是否在位。

用法::

    cd backend
    python -m scripts.eval_gate                 # 默认 gate，回归则 exit 1
    python -m scripts.eval_gate --strict        # 严格模式：hit@5 必须 > baseline
    python -m scripts.eval_gate --update-baseline   # 写 baseline（带 sanity）
    python -m scripts.eval_gate --update-baseline --force  # 强制写
    python -m scripts.eval_gate --json          # 只打印 JSON，不做门控判定

Stage 2 升级（WI-S2.3，PRD D10/D11）
-----------------------------------
* ``--strict``：召回相关代码改动 PR 必须开。hit@5 **严格大于** baseline
  (含 ``_HIT_TOLERANCE``)。CI 自动通过 ``scripts/eval_gate_ci.sh`` 看
  git diff 触发，不靠人工。
* ``--update-baseline``：默认开 sanity 检查 —— 新值若 hit@5 比旧 baseline
  低超过容差、或 token_per_query 超旧值 ×30%，**拒绝写入**（exit 3）。
  ``--force`` 可绕（仅紧急情况）。首次写（无旧 baseline）直接写。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except AttributeError:
        pass

# baseline 文件 —— 钉在 eval 包里，随代码走。
_BASELINE_PATH = (
    Path(__file__).resolve().parent.parent
    / "deskpet" / "memory" / "eval" / "zh_baseline.json"
)
# F2 (memory-stage2-followup)：stage2 用独立 baseline，与 stage1 隔离。
_BASELINE_PATH_STAGE2 = (
    Path(__file__).resolve().parent.parent
    / "deskpet" / "memory" / "eval" / "zh_baseline_stage2.json"
)


def _baseline_path_for_stage(stage: str) -> Path:
    return _BASELINE_PATH_STAGE2 if stage == "stage2" else _BASELINE_PATH


# hit@5 容差：浮点 + mock 向量的抖动留一点余量，但不容真回归。
_HIT_TOLERANCE = 0.02
# token/query 增幅上限（PRD 成功度量）。
_TOKEN_GROWTH_MAX = 1.30


async def run_eval(
    *, top_k: int = 20, stage: str = "stage1", embedder_mode: str = "mock"
) -> dict:
    """seed fixture → embedder backfill → Retriever → MetricsRunner。

    F2 (memory-stage2-followup)：``stage`` 决定召回器与 fixture。
      * ``stage1``（默认，向后兼容）：35 条 message-recall QA + 裸 Retriever。
      * ``stage2``：35 条 + 10 条 entity-targeted QA + EnhancedRetriever
        (enhanced_retriever + entity_path)，让 entity 路真正参与召回，
        ``--strict`` 因此能量化验证 Stage 2 召回提升。

    G5.2 (记忆严测 2026-06-01)：``embedder_mode``。
      * ``"mock"``（默认，向后兼容 + 可复现 gate）：hash 向量，确定性、无权重。
      * ``"real"``：真 BGE-M3（子进程 worker）。用于量化"mock 伪装语义"的
        confound —— 对比 real vs mock 的 hit@5，暴露 mock 数字虚高/虚低。
        需本地有权重；CI gate 跑 real 须确保权重落盘。
    """
    from deskpet.memory.eval.metrics import MetricsRunner
    from deskpet.memory.embedder import Embedder
    from deskpet.memory.retriever import Retriever
    from deskpet.memory.session_db import SessionDB
    from deskpet.memory.vector_worker import VectorWorker

    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "eval_gate.db"

        if stage == "stage2":
            from deskpet.memory.eval.zh_fixture_stage2 import (
                seed_zh_fixture_stage2,
            )
            seed_info = await seed_zh_fixture_stage2(db_path)
        else:
            from deskpet.memory.eval.zh_fixture import seed_zh_fixture
            seed_info = await seed_zh_fixture(db_path)

        sdb = SessionDB(db_path=db_path)
        await sdb.initialize()
        if embedder_mode == "real":
            # 真 BGE-M3：子进程 worker（裸 import 会段错误）。模型路径走 app
            # 自己的解析（paths.user_models_dir 读 DESKPET_MODEL_ROOT / portable
            # / LocalAppData），不再硬编码 C: —— 用户把数据迁到 F 盘后硬编码会
            # 找不到（2026-06-02 踩到）。调用方可设 DESKPET_MODEL_ROOT 覆盖。
            from paths import user_models_dir
            embedder = Embedder(model_path=user_models_dir() / "bge-m3-int8",
                                use_mock_when_missing=False)
        else:
            # mock embedder —— 确定性、无需权重。
            embedder = Embedder(model_path=None, use_mock_when_missing=True)
        await embedder.warmup()
        # 把 fixture 消息的向量补齐，让 Retriever 的 vec 路也参与 RRF。
        vw = VectorWorker(embedder=embedder, session_db=sdb)
        await vw.backfill_missing()

        base = Retriever(session_db=sdb, embedder=embedder)
        if stage == "stage2":
            # 接 Stage 2 召回路径：EnhancedRetriever + facts + entity_path。
            from deskpet.memory.enhanced_retriever import build_recall_retriever
            from deskpet.memory.facts import FactsStore
            from deskpet.memory.entity_extractor import RegexEntityExtractor

            facts_store = FactsStore(db_path)  # mock embedder → entity 走 LIKE
            retriever = build_recall_retriever(
                base,
                rerank=False,
                enhanced_retriever=True,
                query_rewrite=False,
                chunking=False,
                facts_store=facts_store,
                facts_weight=0.2,
                embedder=embedder,
                entity_extractor=RegexEntityExtractor(),
                entity_weight=0.10,
            )
            mode = "eval_gate_stage2"
        else:
            retriever = base
            mode = "eval_gate"

        runner = MetricsRunner(
            db_path, retriever,
            config_snapshot={
                "mode": mode, "embedder": embedder_mode, "stage": stage,
            },
        )
        report = await runner.run(top_k=top_k)
        out = report.as_dict()
        out["_seed"] = seed_info
        out["_embedder_mode"] = embedder_mode
        try:
            await embedder.close()
        except Exception:  # noqa: BLE001
            pass
        return out


def _load_baseline(path: Path | None = None) -> dict | None:
    p = path if path is not None else _BASELINE_PATH
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _gate(current: dict, baseline: dict) -> tuple[bool, list[str]]:
    """返回 (是否通过, 失败原因列表)。"""
    failures: list[str] = []
    base_hit5 = float(baseline.get("hit@5", 0.0))
    cur_hit5 = float(current.get("hit@5", 0.0))
    if cur_hit5 < base_hit5 - _HIT_TOLERANCE:
        failures.append(
            f"hit@5 回归: {cur_hit5:.4f} < baseline {base_hit5:.4f} "
            f"− 容差 {_HIT_TOLERANCE}"
        )
    base_tok = float(baseline.get("token_per_query", 0.0))
    cur_tok = float(current.get("token_per_query", 0.0))
    if base_tok > 0 and cur_tok > base_tok * _TOKEN_GROWTH_MAX:
        failures.append(
            f"token_per_query 超标: {cur_tok:.1f} > baseline {base_tok:.1f} "
            f"× {_TOKEN_GROWTH_MAX}"
        )
    return (not failures), failures


def _gate_strict(current: dict, baseline: dict) -> tuple[bool, list[str]]:
    """Stage 2 严格门控（PRD D10）：hit@5 **严格大于** baseline + 容差。

    召回相关 PR（``*_retriever.py / *_extractor.py / facts.py / chunker.py``
    等）必须开 strict —— 不允许"持平"或"微微下降到容差内"。token 增幅约束
    与默认 gate 一致（≤ +30%）。
    """
    failures: list[str] = []
    base_hit5 = float(baseline.get("hit@5", 0.0))
    cur_hit5 = float(current.get("hit@5", 0.0))
    # 严格大于：要求 cur 必须比 baseline 高出超过 _HIT_TOLERANCE
    if cur_hit5 <= base_hit5 + _HIT_TOLERANCE:
        failures.append(
            f"strict hit@5 未提升: {cur_hit5:.4f} 未 > baseline "
            f"{base_hit5:.4f} + 容差 {_HIT_TOLERANCE}"
        )
    base_tok = float(baseline.get("token_per_query", 0.0))
    cur_tok = float(current.get("token_per_query", 0.0))
    if base_tok > 0 and cur_tok > base_tok * _TOKEN_GROWTH_MAX:
        failures.append(
            f"token_per_query 超标: {cur_tok:.1f} > baseline {base_tok:.1f} "
            f"× {_TOKEN_GROWTH_MAX}"
        )
    return (not failures), failures


def _check_update_sanity(
    current: dict, old: dict | None, force: bool,
) -> tuple[bool, str]:
    """``--update-baseline`` 写入前的 sanity（PRD D11）。

    返回 ``(ok, reason)``。``force=True`` 直接放行；``old is None``（首次写）
    也直接放行。违规：``hit@5`` 比旧值低超过容差，或 ``token_per_query``
    超旧值 × ``_TOKEN_GROWTH_MAX``。
    """
    if force or old is None:
        return True, ""
    base_hit5 = float(old.get("hit@5", 0.0))
    cur_hit5 = float(current.get("hit@5", 0.0))
    if cur_hit5 < base_hit5 - _HIT_TOLERANCE:
        return False, (
            f"拒绝写入: 新 hit@5 {cur_hit5:.4f} < 旧 baseline "
            f"{base_hit5:.4f} − 容差 {_HIT_TOLERANCE}（--force 可绕）"
        )
    base_tok = float(old.get("token_per_query", 0.0))
    cur_tok = float(current.get("token_per_query", 0.0))
    if base_tok > 0 and cur_tok > base_tok * _TOKEN_GROWTH_MAX:
        return False, (
            f"拒绝写入: 新 token_per_query {cur_tok:.1f} > 旧 baseline "
            f"{base_tok:.1f} × {_TOKEN_GROWTH_MAX}（--force 可绕）"
        )
    return True, ""


async def _amain() -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.eval_gate")
    parser.add_argument(
        "--update-baseline", action="store_true",
        help="把当前 eval 结果写成新的 baseline（默认带 sanity 检查）",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="--update-baseline 时绕过 sanity（钉低 hit@5 / 钉高 token）",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="严格门控（PRD D10）：hit@5 必须严格 > baseline + 容差",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="只打印 JSON 结果，跳过门控判定",
    )
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument(
        "--stage", choices=["stage1", "stage2"], default="stage1",
        help="stage1=裸 Retriever(默认)；stage2=EnhancedRetriever+entity_path",
    )
    args = parser.parse_args()

    baseline_path = _baseline_path_for_stage(args.stage)
    current = await run_eval(top_k=args.top_k, stage=args.stage)
    print(json.dumps(current, ensure_ascii=False, indent=2))

    if args.update_baseline:
        old = _load_baseline(baseline_path)
        ok, reason = _check_update_sanity(current, old, force=args.force)
        if not ok:
            print(f"[eval_gate] {reason}", file=sys.stderr)
            return 3
        payload = {
            k: current[k]
            for k in ("qa_set_size", "hit@1", "hit@5", "hit@10",
                      "mrr", "token_per_query")
            if k in current
        }
        baseline_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if args.force and old is not None:
            print(
                f"[eval_gate] baseline 已强制更新（--force） → {baseline_path}"
            )
        else:
            print(f"[eval_gate] baseline 已更新 → {baseline_path}")
        return 0

    if args.json:
        return 0

    baseline = _load_baseline(baseline_path)
    if baseline is None:
        print(
            f"[eval_gate] 无 baseline 文件 ({baseline_path.name}) —— 先跑 "
            f"`python -m scripts.eval_gate --stage={args.stage} "
            f"--update-baseline` 钉一份。",
            file=sys.stderr,
        )
        return 2
    if args.strict:
        ok, failures = _gate_strict(current, baseline)
        gate_name = "strict gate"
    else:
        ok, failures = _gate(current, baseline)
        gate_name = "gate"
    if ok:
        print(f"[eval_gate] PASS —— eval 指标通过 {gate_name}。")
        return 0
    print(
        f"[eval_gate] FAIL —— eval 指标 {gate_name} 未通过：",
        file=sys.stderr,
    )
    for f in failures:
        print(f"  - {f}", file=sys.stderr)
    return 1


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
