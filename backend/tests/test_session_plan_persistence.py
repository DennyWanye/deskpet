# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""FEAT-A4 — plan 消息持久化后端契约测试（superpowers）。

三层契约的后端层（DB sidecar）：

* upsert → get 重建出 awaiting=True + rationale/steps 原样。
* clear_session_plan_awaiting → awaiting=0（plan 行仍在，仅标记翻转）。
* **SW-1 关键**：在「从未调过 ensure_memory_v2_tables 的全新 DB」上，
  upsert 必须自带建表能工作（不靠 fixture 预建 session_plans），否则掩盖
  SW-1（表不存在被静默吞）。

注：``SessionDB.initialize()`` 只建 *base* state schema，不含 memory_v2 的
``session_plans``——故这里不预建该表，专测 upsert 内部 ensure 自建。
"""
from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from deskpet.memory.session_db import SessionDB


async def _table_exists(db_path: Path, name: str) -> bool:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        )
        row = await cur.fetchone()
        await cur.close()
        return row is not None


# ---- SW-1: 全新 DB 上 upsert 自带建表 -----------------------------------

@pytest.mark.asyncio
async def test_upsert_self_creates_table_on_fresh_db(tmp_path: Path):
    """全新 DB（从未 ensure_memory_v2_tables）→ upsert 应自建 session_plans。"""
    db_path = tmp_path / "fresh.db"
    sdb = SessionDB(db_path=str(db_path))
    # 故意不调 ensure_memory_v2_tables，也不预建 session_plans。
    # 表此刻不应存在（除非 initialize 顺带建——下面断言它没建）。

    await sdb.upsert_session_plan(
        "sid-1",
        "重构登录页",
        [{"title": "步骤1", "detail": "读现状"},
         {"title": "步骤2", "detail": "改代码"}],
        True,
    )

    # upsert 内部的 ensure_memory_v2_tables 应已建出该表。
    assert await _table_exists(db_path, "session_plans") is True

    got = await sdb.get_session_plan("sid-1")
    assert got is not None
    assert got["awaiting"] is True
    assert got["rationale"] == "重构登录页"
    assert isinstance(got["steps"], list)
    assert len(got["steps"]) == 2
    assert got["steps"][0]["title"] == "步骤1"


# ---- upsert → get 往返 + 覆盖 ------------------------------------------

@pytest.mark.asyncio
async def test_upsert_get_roundtrip_and_overwrite(tmp_path: Path):
    db_path = tmp_path / "rt.db"
    sdb = SessionDB(db_path=str(db_path))

    await sdb.upsert_session_plan("s", "r1", [{"title": "a", "detail": "x"}], True)
    g1 = await sdb.get_session_plan("s")
    assert g1["rationale"] == "r1"
    assert len(g1["steps"]) == 1

    # 同 session 再 upsert → 覆盖（PK=session_id）。
    await sdb.upsert_session_plan(
        "s", "r2", [{"title": "b", "detail": "y"}, {"title": "c", "detail": "z"}], True,
    )
    g2 = await sdb.get_session_plan("s")
    assert g2["rationale"] == "r2"
    assert len(g2["steps"]) == 2
    assert g2["awaiting"] is True


# ---- clear awaiting ----------------------------------------------------

@pytest.mark.asyncio
async def test_clear_awaiting_flips_flag(tmp_path: Path):
    db_path = tmp_path / "clr.db"
    sdb = SessionDB(db_path=str(db_path))

    await sdb.upsert_session_plan("s", "r", [{"title": "a", "detail": "x"}], True)
    assert (await sdb.get_session_plan("s"))["awaiting"] is True

    await sdb.clear_session_plan_awaiting("s")
    g = await sdb.get_session_plan("s")
    assert g is not None  # 行还在
    assert g["awaiting"] is False  # 标记翻转

    # 幂等：重复 clear 不崩。
    await sdb.clear_session_plan_awaiting("s")
    assert (await sdb.get_session_plan("s"))["awaiting"] is False


@pytest.mark.asyncio
async def test_clear_awaiting_nonexistent_session_is_safe(tmp_path: Path):
    """对不存在的 session clear → 不崩（幂等/无副作用）。"""
    db_path = tmp_path / "none.db"
    sdb = SessionDB(db_path=str(db_path))
    await sdb.clear_session_plan_awaiting("ghost")
    assert await sdb.get_session_plan("ghost") is None


# ---- get 不存在 → None -------------------------------------------------

@pytest.mark.asyncio
async def test_get_nonexistent_returns_none(tmp_path: Path):
    db_path = tmp_path / "empty.db"
    sdb = SessionDB(db_path=str(db_path))
    # get 也走 ensure（自建表）→ 表空 → None。
    assert await sdb.get_session_plan("nope") is None


# ---- awaiting=False 持久化（rehydration 不应重建已确认的 plan）---------

@pytest.mark.asyncio
async def test_upsert_awaiting_false_roundtrip(tmp_path: Path):
    db_path = tmp_path / "af.db"
    sdb = SessionDB(db_path=str(db_path))
    await sdb.upsert_session_plan("s", "r", [], False)
    g = await sdb.get_session_plan("s")
    assert g is not None
    assert g["awaiting"] is False
    assert g["steps"] == []
