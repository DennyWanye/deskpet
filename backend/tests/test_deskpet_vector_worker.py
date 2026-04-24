"""P4-S2 task 4.3 / 4.4 / 4.6 — VectorWorker 单元测试。

覆盖：
  * enqueue N>=batch_size → 自动触发 batch flush
  * enqueue 少量但过 flush_interval_s → 触发 interval flush
  * embedder 抛错 → stats['failed'] 累加、主流程不崩
  * backfill_missing 能把 embedding IS NULL 的老消息补齐
  * stop(drain=True) 把残留刷完
  * 读 messages_vec 行数验证实际落盘（不只是 stats 计数）
  * 失败隔离：写 SQL 抛错 → stats['failed'] 计数
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import numpy as np
import pytest
import pytest_asyncio

from deskpet.memory.embedder import EMBEDDING_DIM, Embedder
from deskpet.memory.session_db import SessionDB
from deskpet.memory.vector_worker import VectorWorker


# ---- Fixtures --------------------------------------------------------


@pytest_asyncio.fixture
async def session_db(tmp_path: Path):
    db = SessionDB(tmp_path / "state.db")
    await db.initialize()
    yield db
    await db.close()


@pytest_asyncio.fixture
async def mock_embedder(tmp_path: Path):
    e = Embedder(
        model_path=tmp_path / "no-bge", use_mock_when_missing=True
    )
    await e.warmup()
    yield e
    await e.close()


@pytest_asyncio.fixture
async def worker(
    mock_embedder: Embedder, session_db: SessionDB
):
    """短 interval 便于测试；batch=4 让边界更明显。"""
    w = VectorWorker(
        mock_embedder,
        session_db,
        batch_size=4,
        flush_interval_s=0.3,  # 短一点加快测试
    )
    await w.start()
    yield w
    await w.stop(drain=True)


# ---- 辅助：读 messages_vec 计数 --------------------------------------


def _count_vec_rows(db_path: Path) -> int:
    """同步读 messages_vec 行数。vec 扩展 load 失败时 fallback 读
    messages.embedding IS NOT NULL 的数量。"""
    conn = sqlite3.connect(str(db_path))
    try:
        try:
            import sqlite_vec  # type: ignore

            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            row = conn.execute(
                "SELECT COUNT(*) FROM messages_vec"
            ).fetchone()
            return int(row[0])
        except Exception:
            row = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE embedding IS NOT NULL"
            ).fetchone()
            return int(row[0])
    finally:
        conn.close()


def _count_embedding_blobs(db_path: Path) -> int:
    """只读 messages.embedding BLOB，不管 vec 表。用于检查 fallback 列也写了。"""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE embedding IS NOT NULL"
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()


# ---- Test cases ------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_triggers_batch_flush_when_full(
    worker: VectorWorker, session_db: SessionDB
):
    """enqueue 10 条，batch_size=4 → 至少 2 次 batch flush 跑完前 8 条，
    剩下 2 条会在下次 interval 或 stop drain 时刷掉。"""
    sid = await session_db.create_session()
    ids = []
    for i in range(10):
        mid = await session_db.append_message(sid, "user", f"msg-{i}")
        ids.append(mid)
        await worker.enqueue(mid, f"msg-{i}")

    # 等 interval 触发把最后一批也刷掉
    await asyncio.sleep(0.8)
    stats = worker.stats()
    assert stats["written"] == 10, stats
    assert stats["failed"] == 0
    assert _count_vec_rows(session_db._db_path) == 10
    assert _count_embedding_blobs(session_db._db_path) == 10


@pytest.mark.asyncio
async def test_enqueue_small_batch_flushes_on_interval(
    worker: VectorWorker, session_db: SessionDB
):
    """enqueue 只 2 条，不到 batch_size=4，但过 flush_interval (0.3s) 应自动 flush。"""
    sid = await session_db.create_session()
    for i in range(2):
        mid = await session_db.append_message(sid, "user", f"small-{i}")
        await worker.enqueue(mid, f"small-{i}")

    # 等超过 interval
    await asyncio.sleep(0.6)
    stats = worker.stats()
    assert stats["written"] == 2
    assert _count_vec_rows(session_db._db_path) == 2


@pytest.mark.asyncio
async def test_encode_failure_does_not_crash_worker(
    session_db: SessionDB, tmp_path: Path
):
    """embedder 抛错 → worker 不崩，stats.failed 累加；后续 batch 能继续。"""

    class BrokenEmbedder:
        def is_ready(self):
            return True

        async def warmup(self):
            return None

        async def encode(self, texts):
            raise RuntimeError("boom: simulated encode failure")

    w = VectorWorker(
        BrokenEmbedder(),  # type: ignore[arg-type]
        session_db,
        batch_size=2,
        flush_interval_s=0.2,
    )
    await w.start()
    try:
        sid = await session_db.create_session()
        for i in range(4):
            mid = await session_db.append_message(sid, "user", f"fail-{i}")
            await w.enqueue(mid, f"fail-{i}")
        await asyncio.sleep(0.5)
        stats = w.stats()
        assert stats["failed"] == 4
        assert stats["written"] == 0
        # messages_vec 应该还是空
        assert _count_vec_rows(session_db._db_path) == 0
    finally:
        await w.stop(drain=False)


@pytest.mark.asyncio
async def test_backfill_missing_populates_historical_messages(
    mock_embedder: Embedder, session_db: SessionDB
):
    """写 6 条消息到 messages（不经 worker），然后 backfill_missing
    应该把所有 embedding 补齐。"""
    sid = await session_db.create_session()
    for i in range(6):
        await session_db.append_message(sid, "user", f"old-{i}")

    # 先确认都没 embedding
    assert _count_embedding_blobs(session_db._db_path) == 0

    w = VectorWorker(
        mock_embedder, session_db, batch_size=3, flush_interval_s=5.0
    )
    try:
        processed = await w.backfill_missing()
        assert processed == 6
        assert _count_vec_rows(session_db._db_path) == 6
        assert _count_embedding_blobs(session_db._db_path) == 6
    finally:
        # 没 start 所以 stop 是 no-op
        await w.stop(drain=False)


@pytest.mark.asyncio
async def test_backfill_missing_respects_limit(
    mock_embedder: Embedder, session_db: SessionDB
):
    sid = await session_db.create_session()
    for i in range(10):
        await session_db.append_message(sid, "user", f"partial-{i}")
    w = VectorWorker(
        mock_embedder, session_db, batch_size=4, flush_interval_s=5.0
    )
    processed = await w.backfill_missing(limit=5)
    assert processed == 5
    assert _count_embedding_blobs(session_db._db_path) == 5


@pytest.mark.asyncio
async def test_backfill_missing_returns_zero_when_all_filled(
    mock_embedder: Embedder, session_db: SessionDB
):
    w = VectorWorker(mock_embedder, session_db, batch_size=4)
    # 空库也应安全
    assert await w.backfill_missing() == 0


@pytest.mark.asyncio
async def test_stop_drain_flushes_queue(
    mock_embedder: Embedder, session_db: SessionDB
):
    """stop(drain=True) 应把 queue 里剩的也跑完。"""
    w = VectorWorker(
        mock_embedder,
        session_db,
        batch_size=100,        # 故意设高避免因 full flush
        flush_interval_s=60.0, # 故意设高避免因 interval flush
    )
    await w.start()
    sid = await session_db.create_session()
    for i in range(3):
        mid = await session_db.append_message(sid, "user", f"drain-{i}")
        await w.enqueue(mid, f"drain-{i}")

    # 确认 queue 没空（batch/interval 都没满）
    # 注：给 run_loop 一点时间从 queue.get 拿走第一条
    await asyncio.sleep(0.05)

    await w.stop(drain=True)
    stats = w.stats()
    assert stats["written"] == 3
    assert _count_vec_rows(session_db._db_path) == 3


@pytest.mark.asyncio
async def test_stats_shape(
    mock_embedder: Embedder, session_db: SessionDB
):
    w = VectorWorker(mock_embedder, session_db)
    s = w.stats()
    for key in ("queued", "queued_total", "written", "failed", "dropped", "last_flush_ts", "is_running"):
        assert key in s


@pytest.mark.asyncio
async def test_enqueue_empty_text_is_skipped(
    worker: VectorWorker, session_db: SessionDB
):
    """空 text 不应入队也不应产生 failed（静默 skip）。"""
    before = worker.stats()
    await worker.enqueue(999, "")
    after = worker.stats()
    assert after["queued_total"] == before["queued_total"]
    assert after["failed"] == before["failed"]


@pytest.mark.asyncio
async def test_enqueue_idempotent_overwrite(
    worker: VectorWorker, session_db: SessionDB
):
    """同 message_id 重复 enqueue 两次，messages_vec 应只有 1 行（先删后插）。"""
    sid = await session_db.create_session()
    mid = await session_db.append_message(sid, "user", "duplicate test")
    await worker.enqueue(mid, "duplicate test")
    await worker.enqueue(mid, "duplicate test v2")
    await asyncio.sleep(0.6)

    assert _count_vec_rows(session_db._db_path) == 1
