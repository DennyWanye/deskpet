"""P4 memory DB migration runner (clean-room, 不共享 P3 代码).

Scope: 负责 ``backend/deskpet/memory/migrations/*.sql`` 的发现、执行、幂等
判定、失败回滚前置（backup）。P3 的 ``backend/memory/migrator.py`` 风格
接近，但两者 **不共享代码**——P3 服务老 memory.db，P4 服务 state.db，生命
周期各自独立。

Conventions（和 P3 对齐以免认知负担）：
  * 每条迁移是 ``<NNN>_<name>.sql`` 单文件，按文件名字典序执行
  * ``schema_migrations(version TEXT PRIMARY KEY, applied_at REAL)``
    表记录已应用的迁移，重跑时 skip
  * ``PRAGMA user_version`` 作为"总体 schema 版本"冗余指针——migration
    文件内部自己 set，便于 ensure_v9 快速判断"库已就位"

Design notes：
  * **Clean-room 起步不经历 v1..v8**。首版 DeskPet 直接 v9，所以 001_p4_initial_v9.sql
    是唯一迁移，内部 ``PRAGMA user_version=9``。未来 v10 再加 002_*.sql 即可。
  * **失败回滚策略在 schema.py 的 initialize_state_db() 里**——本模块只抛
    ``MigrationError``，由调用方决定是不是从 .bak 恢复。

Ref:
  * openspec/changes/p4-poseidon-agent-harness/design.md §D-MIGRATE-1 + R12
  * openspec/changes/p4-poseidon-agent-harness/specs/memory-system/spec.md
    Requirement "Schema Migration v8 → v9"
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

# schema_migrations DDL —— 独立于 P3 的常量。命名一致是故意的：未来如果
# cutover 把 P3 memory 合并进来，schema 语义不变，省一次迁移。
_MIGRATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at REAL NOT NULL
)
"""

# 默认 migration 目录 —— 和本文件同级的 migrations/。
DEFAULT_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# v9 是 P4 的起手目标版本。spec "Schema Migration v8 → v9" 定义。
TARGET_SCHEMA_VERSION = 9


class MigrationError(RuntimeError):
    """Raised when a migration step fails.

    调用方（schema.initialize_state_db）捕获它来触发 .bak 回滚。它本身
    不做任何状态回滚 —— DB 可能处于"部分执行"状态，只能靠外部备份恢复。
    """


def _discover(migrations_dir: Path) -> list[Path]:
    """返回 ``*.sql`` 文件列表，按文件名字典序（NNN_ 前缀保序）。

    目录不存在直接返回空——调用方负责处理"库已 vX 但没迁移文件"的场景。
    """
    if not migrations_dir.exists():
        return []
    return sorted(p for p in migrations_dir.iterdir() if p.suffix == ".sql")


def _safe_timestamp() -> str:
    """返回文件名安全的 ISO8601 时间戳（``:`` 替换成 ``-``）。

    用于 ``.bak.<ts>`` 后缀 —— Windows 文件名不能包含 ``:``。
    """
    now = datetime.now(timezone.utc).replace(microsecond=0)
    # "2026-04-24T12:34:56+00:00" → "2026-04-24T12-34-56"
    return now.isoformat().replace(":", "-").split("+")[0]


async def backup_db(db_path: str | Path) -> Path:
    """复制 ``db_path`` 到 ``<db_path>.bak.<timestamp>`` 并返回备份路径。

    同步 ``shutil.copy2``（保留 mtime）。不存在原库时返回 None？**不**
    ——本函数约定只在"库已存在要迁移"时调用，调用方先检查 exists 再调。

    失败（权限、磁盘满）直接 raise OSError，由上层处理。
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"cannot backup: {db_path} does not exist")
    bak_path = db_path.with_name(f"{db_path.name}.bak.{_safe_timestamp()}")
    # shutil.copy2 保留 mtime；SQLite WAL 附属文件（-wal/-shm）可以不 copy
    # 因为迁移时要求库处于干净 close 状态，WAL 已 checkpoint 回主文件。
    shutil.copy2(db_path, bak_path)
    return bak_path


async def run_migrations(
    db_path: str | Path,
    migrations_dir: Path | None = None,
) -> list[str]:
    """顺序执行所有未应用的迁移文件，返回本次应用的版本列表。

    行为：
      1. 确保父目录存在（tmp_path 场景常见）
      2. 打开 aiosqlite 连接
      3. 建 ``schema_migrations`` 表（幂等）
      4. 读取已应用版本集合
      5. 对每个未应用文件：``executescript`` + ``INSERT schema_migrations``
         + ``commit`` —— 每条迁移独立事务，失败只影响当前条
      6. 任何 SQL 异常 → 抛 ``MigrationError``，当前 commit 不落盘，但
         **之前成功的迁移已落盘**。调用方用 backup 兜底完整 DB 回滚。
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    migrations_dir = migrations_dir or DEFAULT_MIGRATIONS_DIR
    files = _discover(migrations_dir)

    applied_now: list[str] = []
    async with aiosqlite.connect(db_path) as db:
        await db.execute(_MIGRATIONS_TABLE_SQL)
        await db.commit()
        cursor = await db.execute("SELECT version FROM schema_migrations")
        already = {row[0] for row in await cursor.fetchall()}
        await cursor.close()

        for path in files:
            version = path.name  # e.g. "001_p4_initial_v9.sql"
            if version in already:
                continue
            sql = path.read_text(encoding="utf-8")
            try:
                # executescript 跑多语句；隐式 BEGIN/COMMIT 由 aiosqlite
                # 的 isolation 控制，这里显式再 commit 一次保险。
                await db.executescript(sql)
                await db.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, time.time()),
                )
                await db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                log.error(
                    "migration %s failed: %s (db=%s)",
                    version,
                    exc,
                    db_path,
                )
                raise MigrationError(
                    f"migration {version} failed: {exc}"
                ) from exc
            applied_now.append(version)

    return applied_now


async def ensure_v9(
    db_path: str | Path,
    migrations_dir: Path | None = None,
) -> list[str]:
    """启动守门：保证 ``db_path`` 的 schema 至少在 v9。

    流程：
      * 读 ``PRAGMA user_version``
      * ``version == 0`` → 全新库，跑所有 migrations
      * ``0 < version < 9`` → 老 P3 库（理论上 DeskPet 不会遇到；但 spec
        写了 "v8→v9" 所以代码要 defensive）。log warning 然后仍跑增量，
        ``schema_migrations`` 里没记录的文件会被补上。
      * ``version >= 9`` → 正常路径。仍跑 ``run_migrations`` 来确保
        ``schema_migrations`` 和 user_version 一致（比如手动加的 002_*.sql）。
      * 返回本次 applied 的版本列表（可能为空 = 幂等场景）。

    失败：任何子步骤抛 ``MigrationError`` 或 ``sqlite3.Error`` 上抛。
    """
    db_path = Path(db_path)
    # 先单独 open 读 user_version（run_migrations 会自己再 open，
    # 故意分两次以便精确 log）。
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        await cursor.close()
    current = int(row[0]) if row else 0

    if current == 0:
        log.info("state.db fresh (user_version=0), running all migrations")
    elif current < TARGET_SCHEMA_VERSION:
        log.warning(
            "state.db user_version=%d < %d; DeskPet clean-room has no real v1..v8 "
            "history — treating as incremental upgrade and will re-run unapplied SQL",
            current,
            TARGET_SCHEMA_VERSION,
        )
    else:
        log.info(
            "state.db user_version=%d >= %d, applying any pending incremental migrations",
            current,
            TARGET_SCHEMA_VERSION,
        )

    return await run_migrations(db_path, migrations_dir=migrations_dir)
