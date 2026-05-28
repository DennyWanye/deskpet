# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Phase E — tests for ReflectionWorker + SkillMemoryStore.

Coverage:
  ReflectionWorker
    - empty window → returns None, no fact inserted
    - LLM returns empty → returns None
    - LLM raises → returns None (failure-isolation)
    - happy path → fact inserted with category='reflection' + unique key
  SkillMemoryStore
    - add + find_by_name roundtrip
    - list_all ordered by usage_count desc
    - recall matches name / description / trigger_pattern
    - mark_used increments usage_count
    - delete removes the row
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
import pytest_asyncio
import aiosqlite

from deskpet.memory.facts import FactsStore
from deskpet.memory.reflection import (
    ReflectionWorker,
    SkillMemoryStore,
    SkillMemoryEntry,
)
from deskpet.memory.migrator import ensure_v9


@pytest_asyncio.fixture
async def db_path(tmp_path: Path) -> Path:
    db = tmp_path / "state.db"
    await ensure_v9(db)
    async with aiosqlite.connect(db) as conn:
        await conn.execute(
            "INSERT INTO sessions(id, created_at) VALUES ('s1', 1700000000)"
        )
        await conn.commit()
    return db


async def _seed_recent_messages(db_path: Path, n: int = 5) -> None:
    now = time.time()
    async with aiosqlite.connect(db_path) as conn:
        for i in range(n):
            role = "user" if i % 2 == 0 else "assistant"
            await conn.execute(
                "INSERT INTO messages(session_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                ("s1", role, f"message body {i}", now - i * 60),
            )
        await conn.commit()


# ----------------------------------------------------------------------
# ReflectionWorker
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reflection_empty_window_returns_none(db_path: Path) -> None:
    store = FactsStore(db_path)
    worker = ReflectionWorker(db_path, store, llm_call=lambda p: _async_str("note"))
    out = await worker.run_once()
    assert out is None


@pytest.mark.asyncio
async def test_reflection_llm_empty_returns_none(db_path: Path) -> None:
    await _seed_recent_messages(db_path, n=3)
    store = FactsStore(db_path)
    worker = ReflectionWorker(db_path, store, llm_call=lambda p: _async_str("   "))
    out = await worker.run_once()
    assert out is None


@pytest.mark.asyncio
async def test_reflection_llm_failure_returns_none(db_path: Path) -> None:
    await _seed_recent_messages(db_path, n=3)
    store = FactsStore(db_path)
    async def bad(p): raise RuntimeError("LLM 503")
    worker = ReflectionWorker(db_path, store, llm_call=bad)
    out = await worker.run_once()
    assert out is None


@pytest.mark.asyncio
async def test_reflection_happy_path(db_path: Path) -> None:
    await _seed_recent_messages(db_path, n=4)
    store = FactsStore(db_path)
    worker = ReflectionWorker(
        db_path, store,
        llm_call=lambda p: _async_str("User mostly explored memory architecture today."),
    )
    fid = await worker.run_once()
    assert fid is not None and fid > 0
    rows = await store.list_active(category="reflection")
    assert len(rows) == 1
    assert rows[0]["value"].startswith("User mostly explored")
    assert rows[0]["key"].startswith("daily_reflection_")


@pytest.mark.asyncio
async def test_reflection_two_days_two_facts(db_path: Path) -> None:
    await _seed_recent_messages(db_path, n=3)
    store = FactsStore(db_path)
    worker = ReflectionWorker(
        db_path, store, llm_call=lambda p: _async_str("note"),
    )
    fid1 = await worker.run_once(now=1700000000)
    fid2 = await worker.run_once(now=1700000000 + 86400 * 2)
    assert fid1 and fid2 and fid1 != fid2
    rows = await store.list_active(category="reflection")
    assert len(rows) == 2
    keys = {r["key"] for r in rows}
    assert len(keys) == 2  # different date keys


async def _async_str(s: str) -> str:
    return s


# ----------------------------------------------------------------------
# SkillMemoryStore
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skill_add_and_find(db_path: Path) -> None:
    store = SkillMemoryStore(db_path)
    sid = await store.add(SkillMemoryEntry(
        name="deploy_to_staging",
        description="how to push current branch to the staging env",
        trigger_pattern="deploy.*staging",
        steps=["pull main", "merge feature", "push to staging", "smoke test"],
    ))
    assert sid > 0
    found = await store.find_by_name("deploy_to_staging")
    assert found is not None
    assert found["steps"] == [
        "pull main", "merge feature", "push to staging", "smoke test",
    ]
    assert found["usage_count"] == 0


@pytest.mark.asyncio
async def test_skill_list_all_orders_by_usage(db_path: Path) -> None:
    store = SkillMemoryStore(db_path)
    s1 = await store.add(SkillMemoryEntry("A", "x", None, ["step"]))
    s2 = await store.add(SkillMemoryEntry("B", "x", None, ["step"]))
    await store.mark_used(s2)
    await store.mark_used(s2)
    rows = await store.list_all()
    names = [r["name"] for r in rows]
    assert names == ["B", "A"]


@pytest.mark.asyncio
async def test_skill_recall_matches(db_path: Path) -> None:
    store = SkillMemoryStore(db_path)
    await store.add(SkillMemoryEntry(
        name="restart_redis",
        description="restart the local Redis service",
        trigger_pattern="redis.*restart",
        steps=["sudo systemctl restart redis"],
    ))
    await store.add(SkillMemoryEntry(
        name="open_dashboard",
        description="navigate to the metrics dashboard",
        trigger_pattern=None,
        steps=["browser http://localhost:3000"],
    ))
    by_name = await store.recall("redis")
    assert len(by_name) == 1
    assert by_name[0]["name"] == "restart_redis"
    by_desc = await store.recall("metrics")
    assert len(by_desc) == 1
    assert by_desc[0]["name"] == "open_dashboard"
    by_trigger = await store.recall("restart")
    assert len(by_trigger) == 1


@pytest.mark.asyncio
async def test_skill_mark_used_increments(db_path: Path) -> None:
    store = SkillMemoryStore(db_path)
    sid = await store.add(SkillMemoryEntry("X", "y", None, ["s"]))
    await store.mark_used(sid)
    await store.mark_used(sid)
    found = await store.find_by_name("X")
    assert found["usage_count"] == 2
    assert found["last_used_at"] is not None


@pytest.mark.asyncio
async def test_skill_delete(db_path: Path) -> None:
    store = SkillMemoryStore(db_path)
    sid = await store.add(SkillMemoryEntry("X", "y", None, ["s"]))
    assert await store.delete(sid) is True
    assert await store.find_by_name("X") is None
    assert await store.delete(999) is False
