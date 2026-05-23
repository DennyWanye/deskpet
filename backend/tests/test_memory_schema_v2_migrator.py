"""TG-S0 — schema_v2_migrator 测试（Stage 2 / M0）。

针对 PRD/TDD §A0 D1 v2：facts 表加 ``superseded_by`` + ``forgotten_at``
两列，幂等，老库 ALTER + 失败兜底。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import aiosqlite
import pytest

from deskpet.memory.memory_v2_schema import (
    ensure_memory_v2_tables,
    _reset_cache_for_tests,
)
from deskpet.memory.schema_v2_migrator import (
    ensure_memory_v2_columns,
    alter_failures,
    _reset_failures_for_tests,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    _reset_cache_for_tests()
    _reset_failures_for_tests()
    return tmp_path / "state.db"


async def _list_facts_columns(db_path: Path) -> set[str]:
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute("PRAGMA table_info(facts)")
        rows = await cur.fetchall()
        await cur.close()
    return {r[1] for r in rows}


# ---------------------------------------------------------------------------
# TS0-1 — fresh DB: ensure_memory_v2_tables 自带双新列
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ts0_1_fresh_db_has_both_columns(db_path: Path) -> None:
    await ensure_memory_v2_tables(db_path)
    cols = await _list_facts_columns(db_path)
    assert "superseded_by" in cols
    assert "forgotten_at" in cols
    avail = await ensure_memory_v2_columns(db_path)
    assert avail["superseded_by"] is True
    assert avail["forgotten_at"] is True


# ---------------------------------------------------------------------------
# TS0-2 — Stage 1 老库副本：手动建无新列的 facts 表，ensure_columns ALTER 补齐
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ts0_2_legacy_db_gets_altered(db_path: Path) -> None:
    # 模拟 Stage 1 库：手建 facts 表（无 Stage 2 列）。
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            """
            CREATE TABLE facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                subject TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.5,
                source_msg_id INTEGER,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                evidence TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                decay_rate REAL NOT NULL DEFAULT 0.02,
                last_recalled REAL,
                embedding BLOB
            )
            """
        )
        await conn.commit()
    cols = await _list_facts_columns(db_path)
    assert "superseded_by" not in cols
    assert "forgotten_at" not in cols

    avail = await ensure_memory_v2_columns(db_path)
    assert avail == {"superseded_by": True, "forgotten_at": True}
    cols2 = await _list_facts_columns(db_path)
    assert "superseded_by" in cols2
    assert "forgotten_at" in cols2


# ---------------------------------------------------------------------------
# TS0-3 — 重复调用 = no-op
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ts0_3_idempotent(db_path: Path) -> None:
    await ensure_memory_v2_tables(db_path)
    a1 = await ensure_memory_v2_columns(db_path)
    a2 = await ensure_memory_v2_columns(db_path)
    a3 = await ensure_memory_v2_columns(db_path)
    assert a1 == a2 == a3 == {"superseded_by": True, "forgotten_at": True}
    cols = await _list_facts_columns(db_path)
    # 列只出现恰好一次
    assert sum(1 for c in cols if c == "superseded_by") == 1
    assert sum(1 for c in cols if c == "forgotten_at") == 1


# ---------------------------------------------------------------------------
# TS0-4 — ALTER 失败模拟（patch conn.execute 抛错）
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ts0_4_alter_failure_records_unavailable(db_path: Path) -> None:
    # 先建表，但不带新列。
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "CREATE TABLE facts (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
        )
        await conn.commit()

    real_execute = aiosqlite.Connection.execute

    async def _failing_execute(self, sql: str, *args, **kwargs):
        if "ALTER TABLE" in sql.upper():
            raise aiosqlite.Error("simulated ALTER failure")
        return await real_execute(self, sql, *args, **kwargs)

    with patch.object(aiosqlite.Connection, "execute", _failing_execute):
        avail = await ensure_memory_v2_columns(db_path)
    assert avail == {"superseded_by": False, "forgotten_at": False}
    # 全局 _ALTER_FAILURES 同步记录
    failures = alter_failures()
    assert failures.get("superseded_by") is True
    assert failures.get("forgotten_at") is True


# ---------------------------------------------------------------------------
# TS0-5 — 并发 5 个 task 同时跑（safety + 列只 ALTER 一次）
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ts0_5_concurrent_safe(db_path: Path) -> None:
    await ensure_memory_v2_tables(db_path)

    async def runner() -> dict[str, bool]:
        return await ensure_memory_v2_columns(db_path)

    results = await asyncio.gather(*[runner() for _ in range(5)])
    for r in results:
        assert r == {"superseded_by": True, "forgotten_at": True}
    cols = await _list_facts_columns(db_path)
    assert sum(1 for c in cols if c == "superseded_by") == 1
    assert sum(1 for c in cols if c == "forgotten_at") == 1


# ---------------------------------------------------------------------------
# TS0-6 ★v2 — 表不存在 → availability=False，不抛
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ts0_6_table_missing(db_path: Path) -> None:
    # DB 文件存在但无 facts 表。
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("CREATE TABLE unrelated (id INTEGER)")
        await conn.commit()
    avail = await ensure_memory_v2_columns(db_path)
    assert avail == {"superseded_by": False, "forgotten_at": False}
    failures = alter_failures()
    assert failures.get("superseded_by") is True
    assert failures.get("forgotten_at") is True


# ---------------------------------------------------------------------------
# TS0-7 — ensure_memory_v2_tables 直接调用就把列加上（fresh-DB 联调）
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ts0_7_ensure_v2_tables_runs_migrator(db_path: Path) -> None:
    await ensure_memory_v2_tables(db_path)
    cols = await _list_facts_columns(db_path)
    assert "superseded_by" in cols
    assert "forgotten_at" in cols
