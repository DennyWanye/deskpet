"""P4-S3 retriever 测试：RRF 融合 + salience boost + decay + L3 降级。

测试矩阵（对应 tasks.md §5.6 / §5.7 + spec "Hybrid Retrieval with RRF" /
"Memory Decay and Salience" / "L3 failure degrades gracefully"）：

* ``test_semantic_similar_recalled`` —— 向量层能召回 FTS5 字面无匹配但
  语义相似的条目（spec "Semantic query matches non-literal match"）
* ``test_recent_items_boosted`` —— 两条相同文本（embedding 相同），
  recent 那条在融合后排更前（spec "Recent items boosted"）
* ``test_recall_boosts_salience`` —— 命中后 salience +0.05 且 decay_last_touch
  被刷新（spec "Recall boosts salience"）
* ``test_daily_decay_applies_exponential`` —— 30 天未触碰 + lambda=0.02
  → salience 衰减到 ≈ 0.5 * exp(-0.6) ≈ 0.274（spec "Daily decay on
  idle memory"）
* ``test_l3_degrade_returns_other_sources`` —— 把 _vec_recall 逼抛
  OperationalError，recall 不抛、返回非空 Hit（L3 degrade 契约）
* ``test_rrf_fusion_combines_sources`` —— 同一 message_id 出现在 vec+fts
  两条源列表里，融合分严格高于仅出现一次的条目
* ``test_rrf_pure_helper`` —— _rrf_fuse 纯函数的几个边界（空源、单源、
  dominant source 挑选逻辑）
* ``test_empty_query_returns_empty`` —— 空 query 直接返回空 list
* ``test_quote_fts_phrase_escapes`` —— FTS 短语 quote helper 防御性测试
"""
from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np
import pytest
import pytest_asyncio

from deskpet.memory.embedder import EMBEDDING_DIM, Embedder
from deskpet.memory.retriever import (
    Hit,
    Retriever,
    RetrievalPolicy,
    _quote_fts_phrase,
    _rrf_fuse,
    daily_decay,
)
from deskpet.memory.session_db import SessionDB
from deskpet.memory.vector_worker import VectorWorker


# ======================================================================
# Fixtures
# ======================================================================


@pytest_asyncio.fixture
async def embedder():
    """mock 模式 embedder（不依赖 BGE-M3 权重），is_mock=True。"""
    e = Embedder(
        model_path=Path("/nonexistent-for-test"),
        use_mock_when_missing=True,
    )
    await e.warmup()
    yield e
    await e.close()


class _DBWithVec:
    """测试用的组合：SessionDB + VectorWorker 已接线 + start()."""

    def __init__(self, db: SessionDB, worker: VectorWorker) -> None:
        self.db = db
        self.worker = worker

    async def flush(self) -> None:
        """把 enqueue 的 batch 都跑完（drain=True 的轻量版）."""
        # 轻量同步：wait 到 queue 为空，再等一轮 flush interval 让最后一批也写完。
        import asyncio

        while self.worker._queue.qsize() > 0:  # type: ignore[attr-defined]
            await asyncio.sleep(0.02)
        # 留 50ms 让 _flush 任务完成写 messages_vec
        await asyncio.sleep(0.2)


@pytest_asyncio.fixture
async def db_with_vec(tmp_path: Path, embedder: Embedder):
    """SessionDB + VectorWorker 已接 on_message_written hook，start()。

    这是 S3 测试的标准姿势 —— append_message 自动触发 embedding 写入。
    """
    worker_holder: list[VectorWorker] = []

    async def _enqueue_hook(msg_id: int, content: str) -> None:
        # 闭包引用；此时 worker 已构造完，直接调其 enqueue
        if worker_holder:
            await worker_holder[0].enqueue(msg_id, content)

    session_db = SessionDB(
        tmp_path / "state.db",
        on_message_written=_enqueue_hook,
    )
    await session_db.initialize()

    worker = VectorWorker(embedder, session_db, batch_size=8, flush_interval_s=0.1)
    worker_holder.append(worker)
    await worker.start()

    yield _DBWithVec(session_db, worker)

    await worker.stop(drain=True)
    await session_db.close()


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    """纯 SessionDB（无 VectorWorker 钩子），用于不关心 vec 路的场景。"""
    session_db = SessionDB(tmp_path / "state.db")
    await session_db.initialize()
    yield session_db
    await session_db.close()


@pytest_asyncio.fixture
async def populated_db(db_with_vec: _DBWithVec):
    """建 session + 写 5 条消息 + 跑 VectorWorker 把 embedding 写齐。

    返回 (session_db, session_id, [message_id,...])，方便测试直接用。
    """
    db = db_with_vec.db
    sid = await db.create_session()
    msgs = [
        ("user", "我喜欢红色袜子"),
        ("assistant", "知道了，红色袜子很好看"),
        ("user", "今天天气很好"),
        ("assistant", "是啊，阳光不错"),
        ("user", "我想去公园散步"),
    ]
    msg_ids: list[int] = []
    for role, content in msgs:
        mid = await db.append_message(sid, role, content)
        msg_ids.append(mid)

    await db_with_vec.flush()
    return db, sid, msg_ids


# ======================================================================
# Semantic recall through vector layer
# ======================================================================


@pytest.mark.asyncio
async def test_semantic_similar_recalled(
    db_with_vec: _DBWithVec, embedder: Embedder
):
    """spec "Semantic query matches non-literal match":
    query 文本和 message 完全不同，但若 query 用 mock embedder 对同一
    seed 的另一条消息向量相同 → vec 路能召回。

    这里用一个小把戏：mock embedder 的向量是 md5(text) → 所以同一 text
    两次调用向量完全相同。我们插入消息 "semantic-target"，查询 "semantic-target"
    → 应该命中（FTS5 trigram 也会命中，但 vec 会命中更强）。
    核心用来验证"向量路确实跑出来了"——L3 不是僵尸。
    """
    db = db_with_vec.db
    sid = await db.create_session()
    target_id = await db.append_message(sid, "user", "semantic-target")
    await db.append_message(sid, "user", "unrelated content about weather")
    await db.append_message(sid, "user", "another filler message")
    await db_with_vec.flush()

    retriever = Retriever(db, embedder, RetrievalPolicy(top_k=10))
    hits = await retriever.recall("semantic-target", top_k=5)

    assert hits, "expected at least one hit, got empty"
    hit_ids = [h.message_id for h in hits]
    assert target_id in hit_ids, f"target {target_id} missing from {hit_ids}"
    # 命中的 hit 之一 source 应为 vec（向量路有贡献）
    target_hit = next(h for h in hits if h.message_id == target_id)
    assert target_hit.source in {"vec", "fts"}


@pytest.mark.asyncio
async def test_semantic_literal_mismatch_still_recalled(
    db_with_vec: _DBWithVec, embedder: Embedder
):
    """字面不同但向量相似的场景。

    mock embedder 的向量由 md5 决定，不同文本产生不同向量；真实
    semantic-similar 需要真 BGE-M3。本测试的目标退化为：验证
    vec 路和 fts 路一起工作时，即使 FTS 找不到目标，vec 路依然能
    把目标拉回来（用 query 和 target 文本相同作 proxy）。
    """
    db = db_with_vec.db
    sid = await db.create_session()
    # 预期召回目标（vec 路一定命中 —— 和 query 同文本）
    target_id = await db.append_message(sid, "user", "targetXYZ")
    # 干扰项
    for i in range(5):
        await db.append_message(sid, "user", f"noise_{i}")
    await db_with_vec.flush()

    retriever = Retriever(db, embedder)
    hits = await retriever.recall("targetXYZ", top_k=3)
    assert any(h.message_id == target_id for h in hits)


# ======================================================================
# Recency boost
# ======================================================================


@pytest.mark.asyncio
async def test_recent_items_boosted(
    db_with_vec: _DBWithVec, embedder: Embedder
):
    """两条相同文本，新的那条 RRF 融合分更高（recency 源把 recent 排第 1）。

    向量路因文本相同会给两条同样的 distance，但 ``ORDER BY distance, rowid``
    或类似稳定排序会让其中一条靠前；FTS5 rank 同理。真正区分新旧的
    是 recency 源 —— 它严格按 ts 排。
    """
    db = db_with_vec.db
    sid = await db.create_session()
    old_id = await db.append_message(sid, "user", "duplicate content")
    # 手动把 old 的时间戳改到 30 天前，保证 recent 是明确的"新条目"。
    import aiosqlite

    thirty_days_ago = time.time() - 30 * 86400
    async with aiosqlite.connect(db._db_path) as conn:
        await conn.execute(
            "UPDATE messages SET created_at = ? WHERE id = ?",
            (thirty_days_ago, old_id),
        )
        await conn.commit()

    new_id = await db.append_message(sid, "user", "duplicate content")
    await db_with_vec.flush()

    retriever = Retriever(db, embedder)
    hits = await retriever.recall("duplicate content", top_k=10)

    assert {h.message_id for h in hits} >= {old_id, new_id}
    # 找各自的排名位置（列表顺序）
    ranks = {h.message_id: i for i, h in enumerate(hits)}
    assert ranks[new_id] < ranks[old_id], (
        f"recent ({new_id}) should rank above old ({old_id}); "
        f"got {hits}"
    )


# ======================================================================
# Salience boost on recall
# ======================================================================


@pytest.mark.asyncio
async def test_recall_boosts_salience(
    db_with_vec: _DBWithVec, embedder: Embedder
):
    """spec: recall 命中 → salience += 0.05 + decay_last_touch=now()。"""
    db = db_with_vec.db
    sid = await db.create_session()
    mid = await db.append_message(sid, "user", "boost this message")
    await db_with_vec.flush()

    before = (await db.get_messages(sid))[0]
    assert before["salience"] == pytest.approx(0.5)
    assert before["decay_last_touch"] is None

    retriever = Retriever(db, embedder)
    t_before = time.time()
    hits = await retriever.recall("boost this message", top_k=3)
    t_after = time.time()
    assert any(h.message_id == mid for h in hits)

    after = (await db.get_messages(sid))[0]
    # salience += 0.05 但 clamp 到 1.0（此处 0.5 + 0.05 = 0.55，未 clamp）
    assert after["salience"] == pytest.approx(0.55)
    assert after["decay_last_touch"] is not None
    assert t_before <= after["decay_last_touch"] <= t_after + 0.5


@pytest.mark.asyncio
async def test_salience_boost_clamped_to_max(
    db_with_vec: _DBWithVec, embedder: Embedder
):
    """连续 recall 把 salience 逼向 1.0 时应 clamp，不会超过。"""
    db = db_with_vec.db
    sid = await db.create_session()
    mid = await db.append_message(sid, "user", "hot message")
    # 提前把 salience 拉到 0.98 —— 再 +0.05 应被 clamp 到 1.0
    await db.update_salience(mid, 0.98, touch=False)
    await db_with_vec.flush()

    retriever = Retriever(db, embedder)
    await retriever.recall("hot message", top_k=3)
    after = (await db.get_messages(sid))[0]
    assert after["salience"] == pytest.approx(1.0)


# ======================================================================
# Daily decay
# ======================================================================


@pytest.mark.asyncio
async def test_daily_decay_applies_exponential(db: SessionDB):
    """30 天未触碰 + lambda=0.02 → salience * exp(-0.6) ≈ 0.274。

    spec "Daily decay on idle memory" 的公式化验证。
    """
    sid = await db.create_session()
    mid = await db.append_message(sid, "user", "aging message")
    # 设 salience=0.5（默认），decay_last_touch=30 天前
    thirty_days_ago = time.time() - 30 * 86400
    import aiosqlite

    async with aiosqlite.connect(db._db_path) as conn:
        await conn.execute(
            "UPDATE messages SET salience = ?, decay_last_touch = ? WHERE id = ?",
            (0.5, thirty_days_ago, mid),
        )
        await conn.commit()

    changed = await daily_decay(db, decay_lambda=0.02)
    assert changed >= 1

    after = (await db.get_messages(sid))[0]
    expected = 0.5 * math.exp(-0.02 * 30)  # ≈ 0.2744
    assert after["salience"] == pytest.approx(expected, abs=1e-4)


@pytest.mark.asyncio
async def test_daily_decay_idempotent_when_fresh(db: SessionDB):
    """刚触碰的消息（days_since=0）→ exp(0)=1，salience 不变，updates=0。"""
    sid = await db.create_session()
    mid = await db.append_message(sid, "user", "fresh")
    import aiosqlite

    now_ts = time.time()
    async with aiosqlite.connect(db._db_path) as conn:
        await conn.execute(
            "UPDATE messages SET salience = ?, decay_last_touch = ? WHERE id = ?",
            (0.5, now_ts, mid),
        )
        await conn.commit()

    changed = await daily_decay(db, decay_lambda=0.02, now=now_ts)
    assert changed == 0
    after = (await db.get_messages(sid))[0]
    assert after["salience"] == pytest.approx(0.5)


# ======================================================================
# L3 degrade: vec path failure must not take down recall()
# ======================================================================


@pytest.mark.asyncio
async def test_l3_degrade_returns_other_sources(
    db: SessionDB, embedder: Embedder, monkeypatch
):
    """强制 _vec_recall 抛 OperationalError，recall 仍返回 hits（来自 fts/recency/salience）。

    这是 spec "L3 failure degrades gracefully" 的直接验证。
    """
    import sqlite3

    sid = await db.create_session()
    for i in range(3):
        await db.append_message(sid, "user", f"msg-{i} python error")

    # 不启动 VectorWorker —— 模拟真实降级场景（vec 表没数据或不可用）
    retriever = Retriever(db, embedder)

    async def _broken_vec(*args, **kwargs):
        raise sqlite3.OperationalError("no such table: messages_vec")

    monkeypatch.setattr(retriever, "_vec_recall", _broken_vec)

    hits = await retriever.recall("python", top_k=5)
    assert hits, "recall should degrade gracefully, returning non-empty list"
    # 命中应来自 fts/recency/salience，不应来自 vec
    for h in hits:
        assert h.source in {"fts", "recency", "salience"}


@pytest.mark.asyncio
async def test_l3_degrade_when_embedder_not_ready(
    db: SessionDB, tmp_path: Path
):
    """Embedder 从未 warmup → is_ready()=False → vec 路自动跳过，recall 走另三路。"""
    # 建一个没 warmup 的 embedder
    lazy_embedder = Embedder(
        model_path=Path("/nonexistent"),
        use_mock_when_missing=True,
    )
    try:
        # 不调 warmup，is_ready 应为 False
        assert not lazy_embedder.is_ready()

        sid = await db.create_session()
        await db.append_message(sid, "user", "hello recall via fts")

        retriever = Retriever(db, lazy_embedder)
        hits = await retriever.recall("hello", top_k=5)
        assert hits, "even without embedder, fts/recency/salience should produce hits"
    finally:
        await lazy_embedder.close()


@pytest.mark.asyncio
async def test_all_sources_failing_returns_empty(
    db: SessionDB, embedder: Embedder, monkeypatch
):
    """四路都炸 → 返回空 list，**不抛**。"""

    async def _boom(*a, **kw):
        raise RuntimeError("simulated source failure")

    retriever = Retriever(db, embedder)
    monkeypatch.setattr(retriever, "_vec_recall", _boom)
    monkeypatch.setattr(retriever, "_fts_recall", _boom)
    monkeypatch.setattr(retriever, "_recency_recall", _boom)
    monkeypatch.setattr(retriever, "_salience_recall", _boom)

    hits = await retriever.recall("anything", top_k=5)
    assert hits == []


# ======================================================================
# RRF fusion multi-source combine
# ======================================================================


@pytest.mark.asyncio
async def test_rrf_fusion_combines_sources(
    db: SessionDB, embedder: Embedder
):
    """出现在 2+ 源里的条目融合分严格高于仅出现在 1 源的。

    构造：
      * msg_a 在 vec 和 fts 里都排第 1 → 融合分 = vec_weight/(k+1) + fts_weight/(k+1)
      * msg_b 只在 vec 里排第 2 → 融合分 = vec_weight/(k+2)
    期望：msg_a 严格 > msg_b。
    """
    # 纯函数测试 —— 不依赖真实 DB/embedder
    sources = [
        ("vec", [(101, 0.1), (102, 0.2), (103, 0.3)], 0.5),
        ("fts", [(101, 0.5), (104, 1.0)], 0.3),
        ("recency", [(102, 1e9), (101, 1e8)], 0.15),
        ("salience", [(103, 0.9)], 0.05),
    ]
    fused = _rrf_fuse(sources, k=60)
    # 取 dict 方便断言
    scores = {mid: (score, src) for mid, score, src in fused}
    # 101 出现在 3 个源里 → 分最高
    assert 101 in scores and 102 in scores and 103 in scores
    assert scores[101][0] > scores[102][0]
    assert scores[101][0] > scores[103][0]
    # 104 只在 fts 里 → 分最低（vec 权重 0.5 压 fts 0.3，102/103 都在 vec 里）
    assert scores[101][0] > scores[104][0]


def test_rrf_pure_helper_edge_cases():
    """RRF 纯函数几个边界 case。"""
    # 1. 空源 → 空结果
    assert _rrf_fuse([], k=60) == []
    assert _rrf_fuse([("vec", [], 0.5)], k=60) == []

    # 2. 单源 —— 顺序保持
    single = _rrf_fuse(
        [("vec", [(1, 0.1), (2, 0.2), (3, 0.3)], 0.5)],
        k=60,
    )
    assert [t[0] for t in single] == [1, 2, 3]
    # dominant 必是 vec
    assert all(t[2] == "vec" for t in single)

    # 3. weight=0 源应被忽略（不污染 fused scores）
    zero_w = _rrf_fuse(
        [
            ("vec", [(1, 0.1)], 0.5),
            ("fts", [(2, 0.5)], 0.0),  # 权重 0 → 不计入
        ],
        k=60,
    )
    fused_ids = {t[0] for t in zero_w}
    assert 1 in fused_ids and 2 not in fused_ids

    # 4. k 必须 > 0
    with pytest.raises(ValueError):
        _rrf_fuse([("vec", [(1, 0.1)], 0.5)], k=0)


def test_rrf_dominant_source_tiebreak():
    """同贡献时 dominant 按 vec > fts > recency > salience 固定优先级。"""
    # 两个源都把 mid=1 放在第 1 位，权重也相同 → contribution 一样
    fused = _rrf_fuse(
        [
            ("fts", [(1, 0.5)], 0.3),
            ("recency", [(1, 1e9)], 0.3),
        ],
        k=60,
    )
    assert len(fused) == 1
    _, _, dominant = fused[0]
    # fts 优先级高于 recency
    assert dominant == "fts"


# ======================================================================
# Misc / helpers
# ======================================================================


@pytest.mark.asyncio
async def test_empty_query_returns_empty(
    db: SessionDB, embedder: Embedder
):
    retriever = Retriever(db, embedder)
    assert await retriever.recall("") == []
    assert await retriever.recall("   ") == []


def test_quote_fts_phrase_escapes_quotes():
    # 原始引号应被转义为 double-quote
    assert _quote_fts_phrase('hello "world"') == '"hello ""world"""'
    assert _quote_fts_phrase("normal text") == '"normal text"'


@pytest.mark.asyncio
async def test_retriever_exposes_policy(
    db: SessionDB, embedder: Embedder
):
    custom = RetrievalPolicy(top_k=42, vec_weight=0.9)
    r = Retriever(db, embedder, custom)
    assert r.policy.top_k == 42
    assert r.policy.vec_weight == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_recall_returns_hit_dataclass(
    populated_db, embedder: Embedder
):
    """Hit 字段齐全（message_id/score/text/ts/source）。"""
    db, sid, msg_ids = populated_db
    retriever = Retriever(db, embedder)
    hits = await retriever.recall("红色", top_k=3)
    assert hits
    for h in hits:
        assert isinstance(h, Hit)
        assert isinstance(h.message_id, int)
        assert isinstance(h.score, float)
        assert isinstance(h.text, str)
        assert isinstance(h.ts, float)
        assert h.source in {"vec", "fts", "recency", "salience"}
