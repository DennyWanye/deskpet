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
    python -m scripts.eval_gate                 # 跑门控，回归则 exit 1
    python -m scripts.eval_gate --update-baseline   # 把当前结果写成 baseline
    python -m scripts.eval_gate --json          # 只打印 JSON，不做门控判定
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

# hit@5 容差：浮点 + mock 向量的抖动留一点余量，但不容真回归。
_HIT_TOLERANCE = 0.02
# token/query 增幅上限（PRD 成功度量）。
_TOKEN_GROWTH_MAX = 1.30


async def run_eval(*, top_k: int = 20) -> dict:
    """seed fixture → mock embedder backfill → Retriever → MetricsRunner。"""
    from deskpet.memory.eval.metrics import MetricsRunner
    from deskpet.memory.eval.zh_fixture import seed_zh_fixture
    from deskpet.memory.embedder import Embedder
    from deskpet.memory.retriever import Retriever
    from deskpet.memory.session_db import SessionDB
    from deskpet.memory.vector_worker import VectorWorker

    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "eval_gate.db"
        seed_info = await seed_zh_fixture(db_path)

        sdb = SessionDB(db_path=db_path)
        await sdb.initialize()
        # mock embedder —— 确定性、无需权重。
        embedder = Embedder(model_path=None, use_mock_when_missing=True)
        await embedder.warmup()
        # 把 fixture 消息的向量补齐，让 Retriever 的 vec 路也参与 RRF。
        vw = VectorWorker(embedder=embedder, session_db=sdb)
        await vw.backfill_missing()

        retriever = Retriever(session_db=sdb, embedder=embedder)
        runner = MetricsRunner(
            db_path, retriever,
            config_snapshot={"mode": "eval_gate", "embedder": "mock"},
        )
        report = await runner.run(top_k=top_k)
        out = report.as_dict()
        out["_seed"] = seed_info
        return out


def _load_baseline() -> dict | None:
    if not _BASELINE_PATH.exists():
        return None
    try:
        return json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
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


async def _amain() -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.eval_gate")
    parser.add_argument(
        "--update-baseline", action="store_true",
        help="把当前 eval 结果写成新的 baseline",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="只打印 JSON 结果，跳过门控判定",
    )
    parser.add_argument("--top-k", type=int, default=20)
    args = parser.parse_args()

    current = await run_eval(top_k=args.top_k)
    print(json.dumps(current, ensure_ascii=False, indent=2))

    if args.update_baseline:
        payload = {
            k: current[k]
            for k in ("qa_set_size", "hit@1", "hit@5", "hit@10",
                      "mrr", "token_per_query")
            if k in current
        }
        _BASELINE_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"[eval_gate] baseline 已更新 → {_BASELINE_PATH}")
        return 0

    if args.json:
        return 0

    baseline = _load_baseline()
    if baseline is None:
        print(
            "[eval_gate] 无 baseline 文件 —— 先跑 "
            "`python -m scripts.eval_gate --update-baseline` 钉一份。",
            file=sys.stderr,
        )
        return 2
    ok, failures = _gate(current, baseline)
    if ok:
        print("[eval_gate] PASS —— eval 指标未回归。")
        return 0
    print("[eval_gate] FAIL —— eval 指标门控未通过：", file=sys.stderr)
    for f in failures:
        print(f"  - {f}", file=sys.stderr)
    return 1


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
