"""P4-S1 tasks 2.4 — deskpet.memory.migrator unit tests.

Covers:
  * fresh DB initialized to v9（user_version + tables + triggers）
  * idempotent re-run（applied_now 为空，不重复插 schema_migrations）
  * migration error → rollback from .bak（原库内容完整保留）
  * backup 文件名 ISO8601 safe 格式

Requires pytest-asyncio（已在 backend/pyproject.toml [dev] extras）。
所有测试用 tmp_path fixture 避免污染真实 %AppData%\\deskpet\\。
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from deskpet.memory.migrator import (
    MigrationError,
    backup_db,
    ensure_v9,
    run_migrations,
)
from deskpet.memory.schema import InitializeError, initialize_state_db


# ---- Helpers ---------------------------------------------------------


def _list_objects(db_path: Path, obj_type: str) -> list[str]:
    """Return names of SQLite ``type`` objects (table / trigger / index)."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = ? ORDER BY name",
            (obj_type,),
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def _user_version(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


# ---- 2.4.a fresh DB 初始化到 v9 --------------------------------------


@pytest.mark.asyncio
async def test_fresh_db_initializes_v9(tmp_path: Path):
    db = tmp_path / "state.db"
    applied = await run_migrations(db)

    assert applied == ["001_p4_initial_v9.sql"]
    assert _user_version(db) == 9

    tables = _list_objects(db, "table")
    # 不检查 messages_vec（那个由 SessionDB 在运行时按 sqlite-vec 可用性创建）
    assert "messages" in tables
    assert "sessions" in tables
    assert "schema_migrations" in tables

    triggers = _list_objects(db, "trigger")
    assert {"messages_ai", "messages_ad", "messages_au"}.issubset(set(triggers))

    # FTS5 虚拟表在 sqlite_master 里表现为 type='table' name='messages_fts'
    assert "messages_fts" in tables


# ---- 2.4.b 幂等重跑 --------------------------------------------------


@pytest.mark.asyncio
async def test_idempotent_rerun(tmp_path: Path):
    db = tmp_path / "state.db"
    first = await run_migrations(db)
    second = await run_migrations(db)
    third = await ensure_v9(db)

    assert first == ["001_p4_initial_v9.sql"]
    assert second == []
    assert third == []
    # schema_migrations 里只有 1 条
    conn = sqlite3.connect(db)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1


# ---- 2.4.c 失败 → 回滚 .bak -----------------------------------------


@pytest.mark.asyncio
async def test_migration_error_rollback(tmp_path: Path):
    """给 initialize_state_db 塞一个已经 v9 好的库，再指一个坏的
    migrations_dir —— 但 initialize_state_db 默认用 DEFAULT_DIR 我们
    不直接换 dir；改用 monkey-patch 让 ensure_v9 抛 MigrationError。
    断言 .bak 仍保留且主库内容没丢。
    """
    # 先初始化一次得到 v9 DB
    db = tmp_path / "state.db"
    await initialize_state_db(db)
    # 写一条"原数据"进去，用来验证回滚后还在
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO sessions(id, created_at, metadata) VALUES (?, ?, ?)",
            ("sess-original", 1700000000.0, '{"tag":"pre-bad-migration"}'),
        )
        conn.commit()
    finally:
        conn.close()
    pre_bytes = db.read_bytes()

    # monkey-patch: 让下次 initialize_state_db 内的 ensure_v9 抛异常
    import deskpet.memory.schema as schema_mod

    async def _boom(*_args, **_kwargs):
        raise MigrationError("simulated mid-migration failure")

    original = schema_mod.ensure_v9
    schema_mod.ensure_v9 = _boom  # type: ignore[assignment]
    try:
        with pytest.raises(InitializeError):
            await initialize_state_db(db)
    finally:
        schema_mod.ensure_v9 = original  # type: ignore[assignment]

    # 主库应该被 .bak 覆盖回原状
    post_bytes = db.read_bytes()
    assert post_bytes == pre_bytes, "main db was not restored from .bak"

    # 确认原先插入的 session 还在
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT id FROM sessions WHERE id = ?", ("sess-original",)
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("sess-original",)]

    # 至少有一个 .bak 文件存在
    baks = list(tmp_path.glob("state.db.bak.*"))
    assert len(baks) >= 1


# ---- 2.4.d backup 时间戳格式 ----------------------------------------


@pytest.mark.asyncio
async def test_backup_timestamp_format(tmp_path: Path):
    db = tmp_path / "state.db"
    db.write_bytes(b"dummy sqlite content")

    bak = await backup_db(db)

    # 命名：<name>.bak.YYYY-MM-DDTHH-MM-SS
    # 冒号在 Windows 文件名是非法的，必须已被替换成 '-'
    m = re.fullmatch(
        r"state\.db\.bak\.\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}", bak.name
    )
    assert m is not None, f"unexpected backup filename: {bak.name}"
    assert ":" not in bak.name  # Windows 安全
    # 确认父目录和原 db 同级
    assert bak.parent == db.parent
    # 内容是原文件拷贝
    assert bak.read_bytes() == b"dummy sqlite content"


# ---- 2.4.e backup_db 在库不存在时应报错 ------------------------------


@pytest.mark.asyncio
async def test_backup_missing_db_raises(tmp_path: Path):
    db = tmp_path / "does_not_exist.db"
    with pytest.raises(FileNotFoundError):
        await backup_db(db)


# ---- 2.4.f ensure_v9 幂等日志不抛（版本已 >= 9）-----------------------


@pytest.mark.asyncio
async def test_ensure_v9_on_already_v9(tmp_path: Path):
    db = tmp_path / "state.db"
    await run_migrations(db)
    assert _user_version(db) == 9
    # 再跑一次 ensure 不应抛
    applied = await ensure_v9(db)
    assert applied == []
