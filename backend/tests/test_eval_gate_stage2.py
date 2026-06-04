# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""F2 (memory-stage2-followup) — eval_gate stage2 接入测试。

验证：
  1. ``--stage`` baseline 路径解析
  2. stage2 真跑 EnhancedRetriever + entity_path → hit@5 显著高于 stage1
  3. stage2 确定性（regex extractor + LIKE，无随机）
  4. 同 fixture 下 enhanced 显著高于 bare（隔离 entity_path 贡献）
  5. strict gate 在提升时 PASS / 持平时 FAIL
  6. 钉死的 stage2 baseline 存在且高于 stage1 baseline
"""
from __future__ import annotations

import pytest

from scripts import eval_gate


# ---------------------------------------------------------------------------
# baseline 路径解析
# ---------------------------------------------------------------------------
def test_baseline_path_for_stage():
    assert (
        eval_gate._baseline_path_for_stage("stage1").name == "zh_baseline.json"
    )
    assert (
        eval_gate._baseline_path_for_stage("stage2").name
        == "zh_baseline_stage2.json"
    )


# ---------------------------------------------------------------------------
# stage2 真跑：hit@5 显著高于 stage1（entity_path 贡献）
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stage2_beats_stage1_recall():
    s1 = await eval_gate.run_eval(stage="stage1")
    s2 = await eval_gate.run_eval(stage="stage2")
    # stage2 加了 10 条 entity-targeted QA（只有 entity_path 能命中）
    assert s2["qa_set_size"] == s1["qa_set_size"] + 10
    # 召回质量显著提升（F2 验收 ≥ +0.10；实测 +0.1714）
    assert s2["hit@5"] >= s1["hit@5"] + 0.10
    assert s2["hit@1"] >= s1["hit@1"] + 0.10


# ---------------------------------------------------------------------------
# stage2 确定性
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stage2_deterministic():
    r1 = await eval_gate.run_eval(stage="stage2")
    r2 = await eval_gate.run_eval(stage="stage2")
    for k in ("hit@1", "hit@5", "hit@10", "mrr", "qa_set_size"):
        assert r1[k] == r2[k], f"{k} non-deterministic: {r1[k]} vs {r2[k]}"


# ---------------------------------------------------------------------------
# 隔离实验：同一 stage2 fixture（45 题），bare vs enhanced —— 证明提升
# 来自 entity_path 而非"加了简单题"
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stage2_entity_path_is_the_driver(tmp_path):
    from deskpet.memory.eval.zh_fixture_stage2 import seed_zh_fixture_stage2
    from deskpet.memory.eval.metrics import MetricsRunner
    from deskpet.memory.embedder import Embedder
    from deskpet.memory.retriever import Retriever
    from deskpet.memory.session_db import SessionDB
    from deskpet.memory.vector_worker import VectorWorker
    from deskpet.memory.enhanced_retriever import build_recall_retriever
    from deskpet.memory.facts import FactsStore
    from deskpet.memory.entity_extractor import RegexEntityExtractor

    async def _run(enhanced: bool) -> dict:
        db = tmp_path / ("enh.db" if enhanced else "bare.db")
        await seed_zh_fixture_stage2(db)
        sdb = SessionDB(db_path=db)
        await sdb.initialize()
        emb = Embedder(model_path=None, use_mock_when_missing=True)
        await emb.warmup()
        await VectorWorker(embedder=emb, session_db=sdb).backfill_missing()
        base = Retriever(session_db=sdb, embedder=emb)
        if enhanced:
            r = build_recall_retriever(
                base, rerank=False, enhanced_retriever=True,
                query_rewrite=False, chunking=False,
                facts_store=FactsStore(db), facts_weight=0.2, embedder=emb,
                entity_extractor=RegexEntityExtractor(), entity_weight=0.10,
            )
        else:
            r = base
        rep = await MetricsRunner(db, r).run(top_k=20)
        return rep.as_dict()

    bare = await _run(False)
    enh = await _run(True)
    # 同 45 题集，enhanced 显著高于 bare（entity QA 只有 entity_path 能命中）
    assert enh["hit@5"] >= bare["hit@5"] + 0.15


# ---------------------------------------------------------------------------
# strict gate：提升时 PASS，持平时 FAIL
# ---------------------------------------------------------------------------
def test_strict_gate_pass_on_improvement():
    baseline = {"hit@5": 0.50, "token_per_query": 200.0}
    improved = {"hit@5": 0.60, "token_per_query": 205.0}
    ok, failures = eval_gate._gate_strict(improved, baseline)
    assert ok, failures


def test_strict_gate_fail_on_parity():
    baseline = {"hit@5": 0.60, "token_per_query": 195.69}
    parity = {"hit@5": 0.60, "token_per_query": 195.69}
    ok, failures = eval_gate._gate_strict(parity, baseline)
    assert not ok
    assert any("hit@5" in f for f in failures)


# ---------------------------------------------------------------------------
# 钉死的 stage2 baseline 存在且高于 stage1 baseline
# ---------------------------------------------------------------------------
def test_pinned_stage2_baseline_exists_and_higher():
    s1 = eval_gate._load_baseline(eval_gate._baseline_path_for_stage("stage1"))
    s2 = eval_gate._load_baseline(eval_gate._baseline_path_for_stage("stage2"))
    assert s1 is not None, "stage1 baseline missing"
    assert s2 is not None, "stage2 baseline missing"
    assert float(s2["hit@5"]) >= float(s1["hit@5"]) + 0.10
