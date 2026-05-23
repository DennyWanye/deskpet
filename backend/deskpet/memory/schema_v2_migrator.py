"""Additive column migration for memory-v2 tables (Stage 2).

Strategy: ``PRAGMA table_info(<table>)`` introspection + ``ALTER TABLE
ADD COLUMN``. Idempotent. We avoid bumping ``PRAGMA user_version``
(test suite pins it to 16); we also avoid editing the centralized
``_DDL`` for legacy DBs, instead detecting missing columns at runtime.

Stage 2 双列：
  * ``facts.superseded_by INTEGER`` — cross-key 矛盾时指向新 fact 的 id
  * ``facts.forgotten_at REAL``     — memory_forget 标记时间戳

main.py lifespan 调用本模块后读 ``availability`` dict 决定相关 flag
是否要强制关掉（D17 v2 / R8 v2）：ALTER 失败时禁掉 cross_key_merge /
memory_forget，否则后续 SQL 会跑 OperationalError。
"""
from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)


# table -> list[(col_name, ddl_fragment)]
_COLUMN_ADDS: dict[str, list[tuple[str, str]]] = {
    "facts": [
        ("superseded_by", "INTEGER REFERENCES facts(id)"),
        ("forgotten_at", "REAL"),
    ],
}


# 全局记录 ALTER 失败的列名（main.py 据此关 flag + 告警）。
_ALTER_FAILURES: dict[str, bool] = {}


async def ensure_memory_v2_columns(db_path: str | Path) -> dict[str, bool]:
    """Run additive ALTERs for Stage 2 columns. Return availability dict.

    Returns ``{col_name: True/False}``. False = 列不可用（表不存在或
    ALTER 失败），main.py 应据此关掉依赖此列的 flag。

    Failure modes:
      * 表不存在 → 所有列 availability=False（理论上 ensure_memory_v2_tables
        会先把表建出来；走到这里说明调用顺序错了）。
      * ALTER 失败（只读 DB / 权限问题 / 列冲突）→ 该列 availability=False，
        记录到 ``_ALTER_FAILURES``。不抛异常。
    """
    availability: dict[str, bool] = {}
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA busy_timeout=5000")
        for table, cols in _COLUMN_ADDS.items():
            existing = await _list_columns(conn, table)
            if not existing:
                log.warning(
                    "schema_v2_migrator: table %s missing; cannot add columns",
                    table,
                )
                for col_name, _ in cols:
                    availability[col_name] = False
                    _ALTER_FAILURES[col_name] = True
                continue
            for col_name, ddl in cols:
                if col_name in existing:
                    availability[col_name] = True
                    continue
                try:
                    await conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {col_name} {ddl}"
                    )
                    await conn.commit()
                    availability[col_name] = True
                    log.info(
                        "schema_v2_migrator: ALTER TABLE %s ADD %s OK",
                        table, col_name,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "schema_v2_migrator: ALTER %s.%s FAILED: %s",
                        table, col_name, exc,
                    )
                    availability[col_name] = False
                    _ALTER_FAILURES[col_name] = True
    return availability


async def _list_columns(conn: aiosqlite.Connection, table: str) -> set[str]:
    cur = await conn.execute(f"PRAGMA table_info({table})")
    rows = await cur.fetchall()
    await cur.close()
    return {row[1] for row in rows}


def alter_failures() -> dict[str, bool]:
    """Snapshot of columns whose ALTER failed (read-only inspection)."""
    return dict(_ALTER_FAILURES)


def _reset_failures_for_tests() -> None:
    """Test helper. Clears the failure registry. Never call from production."""
    _ALTER_FAILURES.clear()
