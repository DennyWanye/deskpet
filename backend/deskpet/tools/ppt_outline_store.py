# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""PPT Pro 大纲确认卡的等待器与历史持久化。

本模块只提供纯库能力：不在 import 时连库或建表，所有 SQLite 操作都在
调用点懒执行。调用方可以注入 ``conn_factory`` 方便单测；生产侧默认按
SessionDB 使用的 ``state.db`` 位置打开短连接。
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ConnectionFactory = Callable[[], sqlite3.Connection]

_VALID_STATUSES = frozenset(
    {"proposed", "accepted", "rejected", "cancelled", "expired", "superseded"}
)

_DDL_PPT_OUTLINE_HISTORY = """
CREATE TABLE IF NOT EXISTS ppt_outline_history (
    outline_id TEXT PRIMARY KEY,
    session_id TEXT,
    topic TEXT,
    created_at TEXT,
    slides_json TEXT,
    sources_count INTEGER,
    status TEXT
);
"""


class PPTOutlineWaiters:
    """按 outline_id 保存等待用户决策的 Future。

    resolve 不弹出 Future；编排层在 finally 中统一 pop，避免取消路径和确认
    路径双重清理时出现 KeyError。重复 resolve 会因为 Future 已 done 返回
    False，适配双面板重复点击。
    """

    def __init__(self) -> None:
        self._d: dict[str, asyncio.Future] = {}

    def add(self, oid: str, fut: asyncio.Future) -> None:
        self._d[str(oid)] = fut

    def resolve(self, oid: str, decision: dict) -> bool:
        fut = self._d.get(str(oid))
        if fut is None or fut.done():
            return False
        fut.set_result(decision)
        return True

    def pop(self, oid: str) -> asyncio.Future | None:
        return self._d.pop(str(oid), None)


def ensure_ppt_outline_table(conn: sqlite3.Connection) -> None:
    """幂等创建 PPT 大纲历史表。

    这是独立 DDL，不进入共享 schema/migration，保持 flag-off 时字节级 BC。
    """
    if not _outline_history_enabled():
        return
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_DDL_PPT_OUTLINE_HISTORY)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("ppt outline table ensure failed: %s", exc)


def save_outline(
    oid: str,
    sid: str,
    topic: str,
    slides: list,
    sources_count: int,
    *,
    conn_factory: ConnectionFactory | None = None,
) -> bool:
    """保存 proposed 大纲。失败只记录日志并返回 False。"""
    if not _outline_history_enabled():
        return False
    try:
        slides_json = json.dumps(
            [_slide_to_dict(slide) for slide in slides],
            ensure_ascii=False,
        )
        with _managed_conn(conn_factory) as conn:
            ensure_ppt_outline_table(conn)
            conn.execute(
                """
                INSERT OR REPLACE INTO ppt_outline_history(
                    outline_id, session_id, topic, created_at,
                    slides_json, sources_count, status
                ) VALUES (?, ?, ?, ?, ?, ?, 'proposed')
                """,
                (
                    str(oid),
                    str(sid),
                    str(topic),
                    datetime.now().isoformat(),
                    slides_json,
                    int(sources_count),
                ),
            )
            conn.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("ppt outline save failed: %s", exc)
        return False


def mark_status(
    oid: str,
    status: str,
    *,
    conn_factory: ConnectionFactory | None = None,
) -> bool:
    """更新大纲状态。非法状态或 DB 不可用时返回 False。"""
    if not _outline_history_enabled():
        return False
    if status not in _VALID_STATUSES:
        log.warning("ppt outline invalid status: %s", status)
        return False
    try:
        with _managed_conn(conn_factory) as conn:
            ensure_ppt_outline_table(conn)
            cur = conn.execute(
                "UPDATE ppt_outline_history SET status = ? WHERE outline_id = ?",
                (status, str(oid)),
            )
            conn.commit()
            return (cur.rowcount or 0) > 0
    except Exception as exc:  # noqa: BLE001
        log.warning("ppt outline status update failed: %s", exc)
        return False


def list_history(
    sid: str,
    limit: int = 20,
    *,
    conn_factory: ConnectionFactory | None = None,
) -> list[dict]:
    """按创建时间倒序列出某会话的大纲历史，不返回大体积 slides_json。"""
    if not _outline_history_enabled():
        return []
    try:
        safe_limit = max(1, min(int(limit), 100))
        with _managed_conn(conn_factory) as conn:
            ensure_ppt_outline_table(conn)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """
                SELECT outline_id, topic, created_at, sources_count, status
                FROM ppt_outline_history
                WHERE session_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (str(sid), safe_limit),
            )
            rows = cur.fetchall()
        return [_row_to_dict(row) for row in rows]
    except Exception as exc:  # noqa: BLE001
        log.warning("ppt outline history list failed: %s", exc)
        return []


def get_outline(
    oid: str,
    *,
    conn_factory: ConnectionFactory | None = None,
) -> dict | None:
    """读取完整大纲行，包含 slides_json，供历史复用还原。"""
    if not _outline_history_enabled():
        return None
    try:
        with _managed_conn(conn_factory) as conn:
            ensure_ppt_outline_table(conn)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """
                SELECT outline_id, session_id, topic, created_at,
                       slides_json, sources_count, status
                FROM ppt_outline_history
                WHERE outline_id = ?
                """,
                (str(oid),),
            )
            row = cur.fetchone()
        return _row_to_dict(row) if row is not None else None
    except Exception as exc:  # noqa: BLE001
        log.warning("ppt outline get failed: %s", exc)
        return None


def expire_dangling_proposed(
    *,
    conn_factory: ConnectionFactory | None = None,
) -> int:
    """把跨重启残留的 proposed 卡标记为 expired，避免僵尸确认卡。"""
    if not _outline_history_enabled():
        return 0
    try:
        with _managed_conn(conn_factory) as conn:
            ensure_ppt_outline_table(conn)
            cur = conn.execute(
                "UPDATE ppt_outline_history SET status = 'expired' "
                "WHERE status = 'proposed'"
            )
            conn.commit()
            return int(cur.rowcount or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("ppt outline expire failed: %s", exc)
        return 0


def _slide_to_dict(slide: Any) -> dict[str, Any]:
    if dataclasses.is_dataclass(slide):
        return dataclasses.asdict(slide)
    if isinstance(slide, dict):
        return dict(slide)
    raise TypeError(f"unsupported slide type: {type(slide)!r}")


def _outline_history_enabled() -> bool:
    """读取 [ppt].pro_outline_history；读不到配置时默认启用。"""
    try:
        from config import standalone_config_section  # type: ignore[import-not-found]

        raw = standalone_config_section("ppt") or {}
        return bool(raw.get("pro_outline_history", True))
    except Exception:  # noqa: BLE001
        return True


@contextmanager
def _managed_conn(conn_factory: ConnectionFactory | None):
    conn = (conn_factory or _default_conn_factory)()
    try:
        yield conn
    finally:
        conn.close()


def _default_conn_factory() -> sqlite3.Connection:
    path = _default_state_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(path)


def _default_state_db_path() -> Path:
    override = os.environ.get("DESKPET_STATE_DB_PATH") or os.environ.get("DESKPET_STATE_DB")
    if override:
        return Path(override)

    try:
        from config import load_config, resolve_config_path  # type: ignore[import-not-found]

        cfg = load_config(resolve_config_path())
        memory_db = getattr(getattr(cfg, "memory", None), "db_path", "")
        if memory_db:
            return Path(memory_db).resolve().parent / "state.db"
    except Exception as exc:  # noqa: BLE001
        log.debug("ppt outline default state db config lookup failed: %s", exc)

    try:
        import paths as _paths  # type: ignore[import-not-found]

        return _paths.user_data_dir() / "data" / "state.db"
    except Exception:
        return Path("data") / "state.db"


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}
