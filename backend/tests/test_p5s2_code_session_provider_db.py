"""P5-S2 Phase 0 — code_session_provider 数据层测试.

覆盖 OpenSpec change `multi-provider-management` 的 capability
`code-session-provider-binding`：

  * set + get 往返
  * 未绑定 sid 返回 {provider_id: None, preferred_model: None}
  * set 全 None → 行被删
  * migration 文件本身幂等（重跑两次不出错）
  * SessionDB.initialize() 后存在 v14 表 + user_version=14
  * 模拟现有 v13 db 跑 migration 升 v14，不丢老表数据

Spec 引用：
  openspec/changes/multi-provider-management/specs/code-session-provider-binding/spec.md
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from deskpet.memory.session_db import SessionDB
from deskpet.memory.migrator import DEFAULT_MIGRATIONS_DIR, run_migrations


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    """干净的 SessionDB（已 initialize，含 v14 migration）。"""
    s = SessionDB(tmp_path / "state.db")
    await s.initialize()
    yield s
    await s.close()


# ---- 0.2 set + get roundtrip --------------------------------------------


@pytest.mark.asyncio
async def test_set_then_get_binding(db: SessionDB):
    sid = "vpn-tunnel"
    await db.set_code_session_provider_binding(
        sid, provider_id="openrouter-claude", preferred_model=None
    )
    binding = await db.get_code_session_provider_binding(sid)
    assert binding == {
        "provider_id": "openrouter-claude",
        "preferred_model": None,
    }

    # 再设一次（含 model）覆盖
    await db.set_code_session_provider_binding(
        sid, provider_id="openrouter-claude", preferred_model="anthropic/claude-4.7-opus"
    )
    binding2 = await db.get_code_session_provider_binding(sid)
    assert binding2 == {
        "provider_id": "openrouter-claude",
        "preferred_model": "anthropic/claude-4.7-opus",
    }


# ---- 0.3 unbound sid returns null pair -----------------------------------


@pytest.mark.asyncio
async def test_get_unbound_returns_null(db: SessionDB):
    binding = await db.get_code_session_provider_binding("never-bound-sid")
    assert binding == {"provider_id": None, "preferred_model": None}


# ---- 0.4 set 全 None 删行 -----------------------------------------------


@pytest.mark.asyncio
async def test_clear_binding_deletes_row(db: SessionDB, tmp_path: Path):
    sid = "to-clear"
    await db.set_code_session_provider_binding(
        sid, provider_id="some-prov", preferred_model="m"
    )
    # 确认在
    async with aiosqlite.connect(tmp_path / "state.db") as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM code_session_provider WHERE base_session_id=?",
            (sid,),
        )
        assert (await cur.fetchone())[0] == 1

    # 全 None → 删行
    await db.set_code_session_provider_binding(
        sid, provider_id=None, preferred_model=None
    )

    async with aiosqlite.connect(tmp_path / "state.db") as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM code_session_provider WHERE base_session_id=?",
            (sid,),
        )
        assert (await cur.fetchone())[0] == 0

    # get 应该回 null pair（spec scenario "Set provider_id to null"）
    binding = await db.get_code_session_provider_binding(sid)
    assert binding == {"provider_id": None, "preferred_model": None}


# ---- 0.5a fresh install 建表 + user_version=14 -------------------------


@pytest.mark.asyncio
async def test_initialize_creates_v15_table_and_version(tmp_path: Path):
    """SessionDB.initialize() on fresh DB creates code_session_provider
    table and bumps user_version to 14.

    Covers spec scenario "Fresh schema upgrade from v13 to v14".
    """
    db_path = tmp_path / "state.db"
    s = SessionDB(db_path)
    await s.initialize()
    await s.close()

    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute("PRAGMA user_version")
        assert (await cur.fetchone())[0] == 15

        cur = await conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='code_session_provider'"
        )
        row = await cur.fetchone()
        assert row is not None
        ddl = row[0]
        # 必备列
        for col in ("base_session_id", "provider_id", "preferred_model", "updated_at"):
            assert col in ddl


# ---- 0.5 migration 幂等 -------------------------------------------------


@pytest.mark.asyncio
async def test_migration_idempotent(tmp_path: Path):
    """跑两次完整 run_migrations 不出错；第二次不重复 apply 任何文件。"""
    db_path = tmp_path / "state.db"
    first = await run_migrations(db_path)
    # 第二次不该再 apply 任何 sql
    second = await run_migrations(db_path)
    assert second == []

    # 006 必须在 first 里
    assert "007_p5s2_code_session_provider.sql" in first

    # 表必须存在
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='code_session_provider'"
        )
        assert await cur.fetchone() is not None

    # PRAGMA user_version == 15
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute("PRAGMA user_version")
        ver = (await cur.fetchone())[0]
    assert ver == 15


# ---- 0.7 v13 → v14 升级保留老数据 --------------------------------------


@pytest.mark.asyncio
async def test_migration_v14_to_v15_works(tmp_path: Path):
    """模拟 user_version=13 的现有库，跑 migration 后升到 14，老表数据保留。

    实现思路：跑完整 migrations 让库到 v14；写一条 messages 数据；
    然后把 user_version 手工 reset 回 13 并清空 schema_migrations 中
    006 的记录 + drop 006 创建的表，模拟"现网 v13 库 + 新部署带 006"
    情形；再跑一次 run_migrations，确认：
      * 006 执行后 user_version=14
      * 老 messages 数据未丢
      * code_session_provider 表已建好
    """
    db_path = tmp_path / "state.db"

    # 先完整跑 migrations 拿到 v14 schema（含 messages 表等）
    await run_migrations(db_path)

    # 用 SessionDB 写一条老数据进 messages（模拟现网 v13 已有的对话历史）
    s = SessionDB(db_path)
    await s.initialize()
    sid = await s.create_session({"origin": "v13-data"})
    msg_id = await s.append_message(sid, "user", "老 v13 时代的内容")
    await s.close()

    # 模拟 v14 状态：drop 007 创建的表 + 删 schema_migrations 里 007 记录 +
    # user_version 回到 14
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP TABLE IF EXISTS code_session_provider")
        conn.execute(
            "DELETE FROM schema_migrations WHERE version=?",
            ("007_p5s2_code_session_provider.sql",),
        )
        conn.execute("PRAGMA user_version = 14")
        conn.commit()
    finally:
        conn.close()

    # 再跑 migrations，007 应该被 apply
    applied = await run_migrations(db_path)
    assert "007_p5s2_code_session_provider.sql" in applied

    # 验证：user_version 升到 15
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute("PRAGMA user_version")
        ver = (await cur.fetchone())[0]
    assert ver == 15

    # 表已建
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='code_session_provider'"
        )
        assert await cur.fetchone() is not None

    # 老 messages 数据未丢
    s2 = SessionDB(db_path)
    await s2.initialize()
    msgs = await s2.get_messages(sid, limit=10)
    assert len(msgs) == 1
    assert msgs[0]["id"] == msg_id
    assert msgs[0]["content"] == "老 v13 时代的内容"
    await s2.close()
