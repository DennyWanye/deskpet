# SPDX-License-Identifier: BUSL-1.1
"""WI-1.1 — session_goals DDL + SessionDB thin-method round-trip."""
from __future__ import annotations

import aiosqlite
import pytest

from deskpet.memory.memory_v2_schema import (
    ensure_memory_v2_tables,
    ensure_session_goals_table,
    _reset_cache_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_schema_cache():
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


@pytest.mark.asyncio
async def test_session_goals_table_created_with_frozen_columns(tmp_path):
    db = str(tmp_path / "state.db")
    await ensure_session_goals_table(db)
    async with aiosqlite.connect(db) as conn:
        cur = await conn.execute("PRAGMA table_info(session_goals)")
        cols = {row[1] for row in await cur.fetchall()}
    assert cols == {
        "goal_id", "session_id", "text", "status", "progress",
        "criteria", "max_iterations", "iterations_used",
        "set_at", "updated_at",
    }


@pytest.mark.asyncio
async def test_ensure_session_goals_idempotent(tmp_path):
    db = str(tmp_path / "state.db")
    await ensure_session_goals_table(db)
    _reset_cache_for_tests()
    await ensure_session_goals_table(db)


@pytest.mark.asyncio
async def test_shared_ensure_does_NOT_create_session_goals(tmp_path):
    """R-T5 字节基线（单测版）：共享 ensure（facts/session_plans 路径）
    绝不建 session_goals 表——守护 flag-OFF 用户 DB 字节不变护城河。
    """
    db = str(tmp_path / "state.db")
    await ensure_memory_v2_tables(db)
    async with aiosqlite.connect(db) as conn:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='session_goals'"
        )
        row = await cur.fetchone()
    assert row is None


from deskpet.memory.session_db import SessionDB


@pytest.mark.asyncio
async def test_upsert_and_get_active_goal(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    await db.upsert_session_goal(
        goal_id="g1", session_id="s1", text="整理三个会议纪要",
        status="active", progress=0.0, criteria=None,
        max_iterations=10, iterations_used=2,
        set_at=100.0, updated_at=100.0,
    )
    rows = await db.get_active_goals("s1")
    assert len(rows) == 1
    assert rows[0]["text"] == "整理三个会议纪要"
    assert rows[0]["iterations_used"] == 2
    assert rows[0]["status"] == "active"


@pytest.mark.asyncio
async def test_done_goal_excluded_from_active(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    await db.upsert_session_goal(
        goal_id="g1", session_id="s1", text="t", status="done",
        progress=1.0, criteria=None, max_iterations=10,
        iterations_used=1, set_at=1.0, updated_at=2.0,
    )
    assert await db.get_active_goals("s1") == []


@pytest.mark.asyncio
async def test_list_active_goals_across_sessions(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    for i, sid in enumerate(["s1", "s2"]):
        await db.upsert_session_goal(
            goal_id=f"g{i}", session_id=sid, text=f"t{i}",
            status="active", progress=0.0, criteria=None,
            max_iterations=10, iterations_used=0,
            set_at=float(i), updated_at=float(i),
        )
    rows = await db.list_active_goals()
    assert {r["session_id"] for r in rows} == {"s1", "s2"}


@pytest.mark.asyncio
async def test_upsert_overwrites_same_goal_id(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    for used in (1, 5):
        await db.upsert_session_goal(
            goal_id="g1", session_id="s1", text="t", status="active",
            progress=0.0, criteria=None, max_iterations=10,
            iterations_used=used, set_at=1.0, updated_at=float(used),
        )
    rows = await db.get_active_goals("s1")
    assert len(rows) == 1
    assert rows[0]["iterations_used"] == 5
