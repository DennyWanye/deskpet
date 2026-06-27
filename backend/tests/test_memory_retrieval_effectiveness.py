# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""G1 — 检索有效性测试（记忆系统严测 Phase 1）。

## 背景
盘点发现现有 retriever 测试有两类 confound：
1. **只断言非空**：4/14 测试只 `assert hits` 非空，分不清"返回正确条目"和
   "返回任意条目"。RRF 权重清零 / 排序倒置类 bug 能蒙混过测。
2. **mock 伪装语义**：现有 `test_semantic_*` 自己注释承认"真实 semantic-similar
   需要真 BGE-M3"，用同文本 query 当 proxy → 验的是"字面命中"不是"语义命中"。

## 本文件验什么（每条 rank 断言 + 真假 embedder 分轨）
- G1.1 相关项 **排在** 无关项之前（rank 断言，需真 embedder 才有区分度）
- G1.4 recency 排序方向正确（新 > 旧，防倒置）
- G1.5 **真语义召回**（`@model_required` 真 BGE-M3）：query 与目标文本**无重叠**
  但语义近 → 真 embedder 把它排到 top；**mock 对照下 target 不排第一**（证明
  mock 验不了语义）

## 实测得到的两个关键事实（决定断言形态）
1. **vec brute-force 返回所有消息**：vec 路给每条消息算距离，top_k 截断前
   全在 hits 里。所以"无关项完全不出现"**不成立** → 改用 **rank 断言**
   （相关项排在无关项之前）。
2. **mock embedder 无语义区分度**：md5 hash 向量下"红色"查询里"蓝色"
   分数可能更高（实测 id2 0.0115 > id1 0.0113）。所以"区分度/排序正确"
   **只能用真 embedder 验**；mock 版仅验"链路通"。

核心原则：断言"正确排序"而非"非空"；语义/区分度有效性只信真 embedder。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from deskpet.memory.embedder import Embedder
from deskpet.memory.retriever import Retriever, RetrievalPolicy
from deskpet.memory.session_db import SessionDB
from deskpet.memory.vector_worker import VectorWorker


# ======================================================================
# Fixtures（mock embedder 版 + 真 BGE-M3 版）
# ======================================================================
class _DBVec:
    def __init__(self, db: SessionDB, worker: VectorWorker) -> None:
        self.db = db
        self.worker = worker

    async def flush(self) -> None:
        import asyncio
        while self.worker._queue.qsize() > 0:  # type: ignore[attr-defined]
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.25)


async def _make_db_vec(tmp_path: Path, embedder: Embedder) -> _DBVec:
    holder: list[VectorWorker] = []

    async def _hook(msg_id: int, content: str) -> None:
        if holder:
            await holder[0].enqueue(msg_id, content)

    sdb = SessionDB(tmp_path / "state.db", on_message_written=_hook)
    await sdb.initialize()
    worker = VectorWorker(embedder, sdb, batch_size=8, flush_interval_s=0.1)
    holder.append(worker)
    await worker.start()
    return _DBVec(sdb, worker)


@pytest_asyncio.fixture
async def mock_embedder():
    e = Embedder(model_path=Path("/nonexistent"), use_mock_when_missing=True)
    await e.warmup()
    yield e
    await e.close()


@pytest_asyncio.fixture
async def real_embedder():
    """真 BGE-M3（子进程 worker，非裸 import）。仅 model_required 测试用。

    模型路径走 resolver 稳健解析（用户把数据迁到 F 盘后 C: 路径会空 → 此前
    硬编码会整批 ERROR；见 tests/_model_path.py）。找不到 → skip，绝不退回 mock。
    """
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _model_path import resolve_bge_m3

    model = resolve_bge_m3()
    if model is None:
        pytest.skip(
            "BGE-M3 模型未在任何已知位置找到；装模型或设 DESKPET_BGE_M3_DIR 后再跑"
        )
    e = Embedder(model_path=model, use_mock_when_missing=False)
    await e.warmup()
    yield e
    await e.close()


# ======================================================================
# G1.1 — 相关项排在无关项之前（rank 断言，真 embedder 才有区分度）
# ======================================================================
@pytest.mark.asyncio
@pytest.mark.model_required
async def test_g1_1_relevant_ranked_before_irrelevant_real(
    tmp_path: Path, real_embedder: Embedder
) -> None:
    """真 BGE-M3：存"我喜欢红色"(id1) + "我喜欢蓝色"(id2)，query"红色"。

    rank 断言：id1（红色）必须排在 id2（蓝色）之前。
    （vec brute-force 会两条都返回，所以验 rank 不验"排除"。）
    """
    dv = await _make_db_vec(tmp_path, real_embedder)
    try:
        sid = await dv.db.create_session()
        id1 = await dv.db.append_message(sid, "user", "我喜欢红色")
        id2 = await dv.db.append_message(sid, "user", "我喜欢蓝色")
        await dv.flush()

        retriever = Retriever(dv.db, real_embedder, RetrievalPolicy(top_k=10))
        hits = await retriever.recall("红色", top_k=5)
        ids = [h.message_id for h in hits]
        assert id1 in ids, f"相关项'红色'(id={id1}) 应在 hits: {ids}"
        # rank 断言：相关项排在无关项之前（FTS '红色'⊆'我喜欢红色' + vec 语义）
        if id2 in ids:
            assert ids.index(id1) < ids.index(id2), (
                f"相关项'红色'({id1})应排在无关项'蓝色'({id2})之前: {ids}"
            )
    finally:
        await dv.worker.stop(drain=True)
        await dv.db.close()


@pytest.mark.asyncio
async def test_g1_1b_fts_exact_substring_recalls_mock(
    tmp_path: Path, mock_embedder: Embedder
) -> None:
    """mock 版只验"链路通"：FTS 路对精确子串能命中相关项。

    不验区分度（mock 向量无语义），只验 recall 链路返回了含目标的结果。
    """
    dv = await _make_db_vec(tmp_path, mock_embedder)
    try:
        sid = await dv.db.create_session()
        id1 = await dv.db.append_message(sid, "user", "favorite color is red")
        await dv.db.append_message(sid, "user", "the weather today is nice")
        await dv.flush()

        retriever = Retriever(dv.db, mock_embedder, RetrievalPolicy(top_k=10))
        hits = await retriever.recall("red", top_k=5)
        ids = [h.message_id for h in hits]
        # 只验链路通 + 目标在内（FTS trigram 'red'⊆'red'）
        assert id1 in ids, f"FTS 应命中含'red'的目标: {ids}"
    finally:
        await dv.worker.stop(drain=True)
        await dv.db.close()


# ======================================================================
# G1.4 — recency 排序方向正确（新 > 旧，防倒置）
# ======================================================================
@pytest.mark.asyncio
async def test_g1_4_recency_orders_new_before_old(
    tmp_path: Path, mock_embedder: Embedder
) -> None:
    """两条相同文本，新的 rank 必须在旧的之前（防排序倒置 bug）。"""
    import aiosqlite

    dv = await _make_db_vec(tmp_path, mock_embedder)
    try:
        sid = await dv.db.create_session()
        old_id = await dv.db.append_message(sid, "user", "duplicate content")
        # 把 old 时间戳改到很早
        async with aiosqlite.connect(tmp_path / "state.db") as conn:
            await conn.execute(
                "UPDATE messages SET created_at = created_at - 2592000 "
                "WHERE id = ?",
                (old_id,),
            )
            await conn.commit()
        new_id = await dv.db.append_message(sid, "user", "duplicate content")
        await dv.flush()

        retriever = Retriever(dv.db, mock_embedder, RetrievalPolicy(top_k=10))
        hits = await retriever.recall("duplicate content", top_k=5)
        ids = [h.message_id for h in hits]
        assert new_id in ids and old_id in ids, f"两条都该在: {ids}"
        # 方向断言：新条目 rank 严格在旧条目之前
        assert ids.index(new_id) < ids.index(old_id), (
            f"recency 应让新条目({new_id})排在旧条目({old_id})之前: {ids}"
        )
    finally:
        await dv.worker.stop(drain=True)
        await dv.db.close()


# ======================================================================
# G1.5 — 真语义召回（真 BGE-M3）+ mock 对照
# ======================================================================
@pytest.mark.asyncio
@pytest.mark.model_required
async def test_g1_5_real_embedder_semantic_recall(
    tmp_path: Path, real_embedder: Embedder
) -> None:
    """真 BGE-M3：query 与目标**文本无重叠**但语义近 → 应召回。

    这是 mock 永远验不了的：存"我家养了一只橘猫"，query"宠物"——
    "宠物"不在消息文本里（FTS 不命中），只有真语义向量能把它拉回来。
    """
    dv = await _make_db_vec(tmp_path, real_embedder)
    try:
        sid = await dv.db.create_session()
        target = await dv.db.append_message(sid, "user", "我家养了一只橘猫")
        await dv.db.append_message(sid, "user", "今天的股票涨了三个点")
        await dv.db.append_message(sid, "user", "周末打算去爬山")
        await dv.flush()

        retriever = Retriever(dv.db, real_embedder, RetrievalPolicy(top_k=10))
        # "宠物" 与 "橘猫" 文本零重叠，纯语义关联
        hits = await retriever.recall("宠物", top_k=3)
        ids = [h.message_id for h in hits]
        assert target in ids, (
            f"真 BGE-M3 应把语义相关的'我家养了一只橘猫'(id={target})"
            f"召回（query'宠物'文本无重叠）: {ids}"
        )
    finally:
        await dv.worker.stop(drain=True)
        await dv.db.close()


@pytest.mark.asyncio
async def test_g1_5b_mock_embedder_no_semantic_discrimination_control(
    tmp_path: Path, mock_embedder: Embedder
) -> None:
    """对照组：mock embedder 下"宠物"无法把"橘猫"排到 top-1。

    vec brute-force 会返回所有消息（含 target），但 mock 向量无语义 →
    target 排第一纯属 hash 巧合。这里断言 **mock 下 target 不稳定排第一**
    （3 个无关消息，随机 hash 让 target 排第一概率仅 1/3）。
    用"target 不在 top-1"作对照：证明真信心只能来自 G1.5 真 embedder 版。

    注：mock 是确定性 hash，结果固定。实测 mock 下"宠物"的 top-1 不是橘猫。
    若哪天 hash 巧合让它排第一，本断言会失败 —— 那时改用更多干扰项稀释。
    """
    dv = await _make_db_vec(tmp_path, mock_embedder)
    try:
        sid = await dv.db.create_session()
        target = await dv.db.append_message(sid, "user", "我家养了一只橘猫")
        await dv.db.append_message(sid, "user", "今天的股票涨了三个点")
        await dv.db.append_message(sid, "user", "周末打算去爬山看风景")
        await dv.flush()

        retriever = Retriever(dv.db, mock_embedder, RetrievalPolicy(top_k=10))
        hits = await retriever.recall("宠物", top_k=3)
        ids = [h.message_id for h in hits]
        # 对照断言：mock 下 target 不该稳定排第一（无语义 → top-1 是 hash 噪声）
        assert ids[0] != target, (
            "mock embedder 把'橘猫'排第一了 —— 这是 hash 巧合不是语义。"
            f"对照失效（说明该用真 embedder 验语义）: top-1={ids[0]}, target={target}"
        )
    finally:
        await dv.worker.stop(drain=True)
        await dv.db.close()
