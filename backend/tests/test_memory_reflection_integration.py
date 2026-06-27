# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""TG-7 — reflection + skill memory 集成测试（WI-M1.7）。

ReflectionWorker.run_once 产物写进 facts 表（category=reflection）；
SkillMemoryStore 独立 CRUD；无 LLM 时跳过不报错；flag 默认关。
"""
from __future__ import annotations

import aiosqlite
import pytest

from deskpet.memory.session_db import SessionDB
from deskpet.memory.facts import FactsStore
from deskpet.memory.reflection import (
    ReflectionWorker,
    SkillMemoryStore,
    SkillMemoryEntry,
)
from deskpet.memory.memory_v2_schema import _reset_cache_for_tests
from config import MemoryV2Config


@pytest.fixture
def db_path(tmp_path):
    _reset_cache_for_tests()
    return tmp_path / "state.db"


async def _seed_messages(db_path):
    sdb = SessionDB(db_path=db_path)
    await sdb.initialize()
    await sdb.append_message(session_id="s1", role="user", content="我今天在写记忆系统的升级")
    await sdb.append_message(
        session_id="s1", role="assistant", content="升级 memory-v2 是个不错的进展。",
    )


# --- T7-1：reflection 产物写进 facts 表 category=reflection -------------
@pytest.mark.asyncio
async def test_t7_1_reflection_writes_fact(db_path):
    await _seed_messages(db_path)
    store = FactsStore(db_path)

    async def _note_llm(prompt: str) -> str:
        return "用户今天专注于记忆系统升级，进展积极。"

    worker = ReflectionWorker(db_path, store, _note_llm)
    fid = await worker.run_once()
    assert isinstance(fid, int) and fid > 0
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM facts WHERE category = 'reflection'"
        )
        rows = [dict(r) for r in await cur.fetchall()]
        await cur.close()
    assert len(rows) == 1
    assert "记忆系统" in rows[0]["value"]


# --- T7-2：SkillMemoryStore 独立 CRUD（与 ReflectionWorker 无耦合）-----
@pytest.mark.asyncio
async def test_t7_2_skill_memory_store_crud(db_path):
    store = SkillMemoryStore(db_path)
    sid = await store.add(SkillMemoryEntry(
        name="重启后端", description="后端崩了就 taskkill + 重启",
        trigger_pattern="后端.*崩", steps=["taskkill deskpet.exe", "重新启动"],
    ))
    assert sid > 0
    found = await store.find_by_name("重启后端")
    assert found is not None and found["steps"] == ["taskkill deskpet.exe", "重新启动"]
    hits = await store.recall("后端")
    assert any(h["name"] == "重启后端" for h in hits)
    assert await store.delete(sid) is True


# --- T7-3：reflection flag 默认关（lifespan 据此不注册定时任务）--------
def test_t7_3_reflection_flag_defaults_off():
    assert MemoryV2Config().reflection is False


# --- T7-4：reflection 跑时无可用 LLM → 跳过本次，不报错 ----------------
@pytest.mark.asyncio
async def test_t7_4_reflection_no_llm_skips_gracefully(db_path):
    await _seed_messages(db_path)
    store = FactsStore(db_path)

    async def _broken_llm(prompt: str) -> str:
        raise RuntimeError("llm offline")

    worker = ReflectionWorker(db_path, store, _broken_llm)
    fid = await worker.run_once()  # 不应抛
    assert fid is None
