"""TG-8 — eval 回归门控测试（WI-M0.2）。

中文回测集 fixture 可复现；EvalReport 带 token_per_query；eval_gate 的
门控判定能在指标回归时报 FAIL。
"""
from __future__ import annotations

import sys
from pathlib import Path

import aiosqlite
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from deskpet.memory.eval.zh_fixture import seed_zh_fixture, FIXTURE_SOURCE
from deskpet.memory.eval.metrics import MetricsRunner
from deskpet.memory.session_db import SessionDB
from deskpet.memory.embedder import Embedder
from deskpet.memory.retriever import Retriever
from deskpet.memory.memory_v2_schema import _reset_cache_for_tests


@pytest.fixture
def db_path(tmp_path):
    _reset_cache_for_tests()
    return tmp_path / "state.db"


# --- T8-1：中文 fixture 造出可复现回测集 -------------------------------
@pytest.mark.asyncio
async def test_t8_1_zh_fixture_seeds_qa_set(db_path):
    info = await seed_zh_fixture(db_path)
    assert info["messages"] == 40
    assert info["qa"] >= 30
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM memory_qa_set WHERE source = ?",
            (FIXTURE_SOURCE,),
        )
        n = (await cur.fetchone())[0]
        await cur.close()
    assert n == info["qa"]


# --- T8-2：eval run 产出 token_per_query --------------------------------
@pytest.mark.asyncio
async def test_t8_2_eval_report_has_token_per_query(db_path):
    await seed_zh_fixture(db_path)
    sdb = SessionDB(db_path=db_path)
    await sdb.initialize()
    embedder = Embedder(model_path=None, use_mock_when_missing=True)
    retriever = Retriever(session_db=sdb, embedder=embedder)
    report = await MetricsRunner(db_path, retriever).run(top_k=20)
    assert report.qa_set_size >= 30
    d = report.as_dict()
    for k in ("hit@1", "hit@5", "hit@10", "mrr", "token_per_query"):
        assert k in d, k
    assert report.token_per_query >= 0.0


# --- T8-6：eval_gate 门控判定能识别回归 ---------------------------------
def test_t8_6_eval_gate_detects_regression():
    import eval_gate

    baseline = {"hit@5": 0.40, "token_per_query": 200.0}
    # 无回归
    ok, fails = eval_gate._gate(
        {"hit@5": 0.41, "token_per_query": 210.0}, baseline,
    )
    assert ok and not fails
    # hit@5 回归
    ok, fails = eval_gate._gate(
        {"hit@5": 0.30, "token_per_query": 200.0}, baseline,
    )
    assert not ok and any("hit@5" in f for f in fails)
    # token 超 +30%
    ok, fails = eval_gate._gate(
        {"hit@5": 0.41, "token_per_query": 300.0}, baseline,
    )
    assert not ok and any("token" in f for f in fails)


# --- T8-6b：baseline 文件存在且字段完整 ---------------------------------
def test_t8_6b_baseline_file_present():
    import json
    p = (
        Path(__file__).resolve().parent.parent
        / "deskpet" / "memory" / "eval" / "zh_baseline.json"
    )
    assert p.exists(), "STAGE0 baseline 文件应随代码提交"
    data = json.loads(p.read_text(encoding="utf-8"))
    for k in ("hit@5", "token_per_query"):
        assert k in data
