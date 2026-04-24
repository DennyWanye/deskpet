"""P4-S1 task 3.5 — SessionDB 性能回归测试（默认不跑）。

Mark: ``@pytest.mark.perf`` —— 默认被 ``pyproject.toml`` 的
``addopts = "-m 'not perf'"`` 过滤掉。手动触发：

    pytest -m perf backend/tests/test_deskpet_session_db_perf.py -v

目标：
  * FTS5 10K 条数据 search p95 < 50ms（spec 的 5ms 那是 100K 目标，
    实战 10K 50ms 更合理；精细 SLO 在 P4-S12 卡）
  * 两个 asyncio task 并发写 200 条（各 100），全部成功、无 SQLITE_BUSY 漏损

注意：这些数字是 dev 机上 ballpark，CI 机器可能更慢/更快。P4-S12
会跑 `scripts/bench_phase4.py` 做最终校验。
"""
from __future__ import annotations

import asyncio
import statistics
import time
from pathlib import Path

import pytest
import pytest_asyncio

from deskpet.memory.session_db import SessionDB


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    """fresh SessionDB for each perf test."""
    d = SessionDB(tmp_path / "state.db")
    await d.initialize()
    yield d
    await d.close()


@pytest.mark.perf
@pytest.mark.asyncio
async def test_fts5_10k_messages(db: SessionDB):
    """Bulk insert 10K messages, then measure 10 FTS5 search timings."""
    import aiosqlite

    sid = await db.create_session()

    # Bulk insert via direct aiosqlite (绕开 append_message 的 lock 加速 10K)
    # 用一个事务，触发器会同步 FTS 索引。
    rows = [
        (
            sid,
            "user" if i % 2 == 0 else "assistant",
            f"sample message {i} discussing python programming keyword {i % 100}",
            1700000000.0 + i,
        )
        for i in range(10_000)
    ]
    async with aiosqlite.connect(db._db_path) as conn:  # noqa: SLF001
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.executemany(
            "INSERT INTO messages(session_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            rows,
        )
        await conn.commit()

    # warm up
    await db.search_fts("keyword")

    timings_ms: list[float] = []
    for _ in range(10):
        t0 = time.perf_counter()
        hits = await db.search_fts("python programming", limit=20)
        t1 = time.perf_counter()
        timings_ms.append((t1 - t0) * 1000.0)
        # 确保每次都真的返回了东西
        assert len(hits) > 0

    timings_ms.sort()
    median = statistics.median(timings_ms)
    # index 8 of 10 sorted timings ≈ p90；我们按样本边界取 p95
    p95 = timings_ms[min(len(timings_ms) - 1, int(len(timings_ms) * 0.95))]
    print(
        f"\nFTS5 10K search: median={median:.2f}ms, p95={p95:.2f}ms, "
        f"all={[f'{x:.1f}' for x in timings_ms]}"
    )
    assert p95 < 50.0, f"FTS5 p95 {p95:.2f}ms >= 50ms target"


@pytest.mark.perf
@pytest.mark.asyncio
async def test_concurrent_write_retry(db: SessionDB):
    """两个 asyncio task 同时各写 100 条 —— 在 SessionDB._write_lock +
    busy_timeout + 应用层 retry 三道防线后，应全部成功。"""
    sid = await db.create_session()

    async def writer(tag: str, n: int) -> int:
        ok = 0
        for i in range(n):
            await db.append_message(sid, "user", f"{tag}-{i}")
            ok += 1
        return ok

    n = 100
    t0 = time.perf_counter()
    results = await asyncio.gather(writer("A", n), writer("B", n))
    elapsed = time.perf_counter() - t0

    assert results == [n, n]
    # 通过 get_messages 拉取校验总数
    total = len(await db.get_messages(sid, limit=10_000))
    assert total == 2 * n
    print(
        f"\nconcurrent write {2*n} msgs in {elapsed*1000:.0f}ms "
        f"({(2*n)/elapsed:.0f} msg/s)"
    )
