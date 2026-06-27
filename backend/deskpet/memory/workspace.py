# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Phase D — Per-session workspace memory.

What
----
``code mode`` agents in long tasks frequently re-``read_file`` files
they already wrote or examined earlier in the session. The original
fix was "remember more in context", but with 1+ hour sessions and
hundreds of tool calls the context window is the wrong place — we
need a **persisted scratchpad** keyed by (session_id, path).

Schema (created by migration 009 ``workspace_state``):

  session_id       TEXT
  path             TEXT
  last_action      TEXT      'read' | 'write' | 'edit' | 'delete'
  last_action_ts   REAL
  content_hash     TEXT      sha1 — change-detection for external edits
  content_summary  TEXT      optional one-liner from LLM
  byte_size        INTEGER
  PRIMARY KEY (session_id, path)

Public surface
--------------
:class:`WorkspaceMemoryStore` exposes CRUD + a ``recall(query)`` method
returning recent files matching a substring — wired by
``WorkspaceMemoryComponent`` into the assembler bundle, and by the
``workspace_recall`` builtin tool the agent can invoke directly.

Hooks: ``record_action(session_id, path, action, content=None)`` is
called from the file_read / file_write / file_edit tool wrappers.

Failure-isolation: all methods catch :class:`aiosqlite.Error` and log
a debug warning; the chat path is **never** broken by workspace memory
hiccups.
"""
from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from deskpet.memory.memory_v2_schema import ensure_memory_v2_tables

log = logging.getLogger(__name__)


_VALID_ACTIONS = frozenset({"read", "write", "edit", "delete"})


async def _ensure_schema(db_path: Path) -> None:
    await ensure_memory_v2_tables(db_path)


def _content_hash(content: str) -> str:
    return hashlib.sha1(content.encode("utf-8", errors="replace")).hexdigest()


def _summarize_for_recall(path: str, content: str, max_chars: int = 120) -> str:
    """Produce a short content snippet for recall display.

    No LLM call here — Phase D minimum viable just keeps the first
    non-blank line. ``ReflectionWorker`` (Phase E) can asynchronously
    upgrade these summaries with LLM-generated descriptions.
    """
    if not content:
        return ""
    for line in content.splitlines():
        line = line.strip()
        if line:
            return line[:max_chars]
    return content.strip()[:max_chars]


class WorkspaceMemoryStore:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)

    async def record_action(
        self,
        *,
        session_id: str,
        path: str,
        action: str,
        content: Optional[str] = None,
        now: Optional[float] = None,
    ) -> None:
        """Upsert a workspace_state row for the (session_id, path) key.

        For ``read`` actions, we don't update content_summary unless we
        have content + the row doesn't already have a summary — reads
        are cheap, writes are the interesting events.
        """
        if action not in _VALID_ACTIONS:
            raise ValueError(f"action must be one of {sorted(_VALID_ACTIONS)}, got {action!r}")
        await _ensure_schema(self._db_path)
        ts = now if now is not None else time.time()

        new_hash: Optional[str] = None
        new_summary: Optional[str] = None
        new_size: Optional[int] = None
        if content is not None:
            new_hash = _content_hash(content)
            new_summary = _summarize_for_recall(path, content)
            new_size = len(content)

        try:
            async with aiosqlite.connect(self._db_path) as conn:
                await conn.execute("PRAGMA busy_timeout=5000")
                conn.row_factory = aiosqlite.Row
                cur = await conn.execute(
                    "SELECT content_summary FROM workspace_state "
                    "WHERE session_id = ? AND path = ?",
                    (session_id, path),
                )
                existing = await cur.fetchone()
                await cur.close()

                preserved_summary: Optional[str] = None
                if existing is not None:
                    preserved_summary = existing["content_summary"]

                final_summary = new_summary
                if action == "read" and new_summary is None and preserved_summary:
                    final_summary = preserved_summary
                elif action == "read" and preserved_summary and new_summary is None:
                    final_summary = preserved_summary

                # Build upsert. SQLite supports ON CONFLICT REPLACE since
                # 3.24 — we use UPSERT semantics for clarity.
                await conn.execute(
                    "INSERT INTO workspace_state("
                    "session_id, path, last_action, last_action_ts, "
                    "content_hash, content_summary, byte_size"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(session_id, path) DO UPDATE SET "
                    "  last_action = excluded.last_action, "
                    "  last_action_ts = excluded.last_action_ts, "
                    "  content_hash = COALESCE(excluded.content_hash, workspace_state.content_hash), "
                    "  content_summary = COALESCE(excluded.content_summary, workspace_state.content_summary), "
                    "  byte_size = COALESCE(excluded.byte_size, workspace_state.byte_size)",
                    (
                        session_id, path, action, ts,
                        new_hash, final_summary, new_size,
                    ),
                )
                await conn.commit()
        except aiosqlite.Error as exc:
            log.debug("workspace.record_action sqlite error: %s", exc)

    async def get(
        self, *, session_id: str, path: str
    ) -> Optional[dict[str, Any]]:
        await _ensure_schema(self._db_path)
        try:
            async with aiosqlite.connect(self._db_path) as conn:
                conn.row_factory = aiosqlite.Row
                cur = await conn.execute(
                    "SELECT * FROM workspace_state "
                    "WHERE session_id = ? AND path = ?",
                    (session_id, path),
                )
                row = await cur.fetchone()
                await cur.close()
        except aiosqlite.Error as exc:
            log.debug("workspace.get sqlite error: %s", exc)
            return None
        return dict(row) if row else None

    async def list_session(
        self,
        session_id: str,
        *,
        actions: Optional[list[str]] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Recent files touched in this session, newest first."""
        await _ensure_schema(self._db_path)
        where = ["session_id = ?"]
        params: list[Any] = [session_id]
        if actions:
            placeholders = ",".join("?" for _ in actions)
            where.append(f"last_action IN ({placeholders})")
            params.extend(actions)
        params.append(int(limit))
        try:
            async with aiosqlite.connect(self._db_path) as conn:
                conn.row_factory = aiosqlite.Row
                cur = await conn.execute(
                    "SELECT * FROM workspace_state "
                    f"WHERE {' AND '.join(where)} "
                    "ORDER BY last_action_ts DESC LIMIT ?",
                    tuple(params),
                )
                rows = await cur.fetchall()
                await cur.close()
        except aiosqlite.Error as exc:
            log.debug("workspace.list_session sqlite error: %s", exc)
            return []
        return [dict(r) for r in rows]

    async def recall(
        self,
        query: str,
        *,
        session_id: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """LIKE-based search across path + content_summary.

        Used by the ``workspace_recall`` builtin tool — agents call this
        when they need to remember "what file did I create for X".
        """
        if not query or not query.strip():
            return []
        await _ensure_schema(self._db_path)
        # F5 修复（2026-06-01）：旧实现 `path/summary LIKE '%整个 query%'`，
        # 自然语言 query 几乎永远 0 命中（真机终验暴露：agent 传
        # "...README.md file access read touch"，path 不含整串）。改为分词
        # OR LIKE（path / content_summary），按命中词数降序、recency 次之。
        # 分词 LIKE 只修"词在但整串不匹配"；纯语义须走向量路（调用方负责）。
        from deskpet.memory.text_tokenize import tokenize_query

        tokens = tokenize_query(query)
        if not tokens:
            tokens = [query.strip()]  # 分词空 → 整串保底

        score_terms = []
        match_terms = []
        params: list[Any] = []
        for t in tokens:
            pat = f"%{t}%"
            score_terms.append(
                "(CASE WHEN (path LIKE ? OR COALESCE(content_summary,'') LIKE ?) "
                "THEN 1 ELSE 0 END)"
            )
            params.extend([pat, pat])
            match_terms.append(
                "(path LIKE ? OR COALESCE(content_summary,'') LIKE ?)"
            )
            params.extend([pat, pat])
        match_score = " + ".join(score_terms)
        where = [f"({' OR '.join(match_terms)})"]
        if session_id:
            where.append("session_id = ?")
            params.append(session_id)
        params.append(int(limit))
        sql = (
            f"SELECT *, ({match_score}) AS _match_score FROM workspace_state "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY _match_score DESC, last_action_ts DESC LIMIT ?"
        )
        try:
            async with aiosqlite.connect(self._db_path) as conn:
                conn.row_factory = aiosqlite.Row
                cur = await conn.execute(sql, tuple(params))
                rows = await cur.fetchall()
                await cur.close()
        except aiosqlite.Error as exc:
            log.debug("workspace.recall sqlite error: %s", exc)
            return []
        out = []
        for r in rows:
            d = dict(r)
            d.pop("_match_score", None)
            out.append(d)
        return out

    async def forget_session(self, session_id: str) -> int:
        await _ensure_schema(self._db_path)
        try:
            async with aiosqlite.connect(self._db_path) as conn:
                cur = await conn.execute(
                    "DELETE FROM workspace_state WHERE session_id = ?",
                    (session_id,),
                )
                n = cur.rowcount or 0
                await cur.close()
                await conn.commit()
            return int(n)
        except aiosqlite.Error as exc:
            log.debug("workspace.forget_session sqlite error: %s", exc)
            return 0

    async def stale_external_edits(
        self,
        *,
        session_id: str,
        path: str,
        current_content: str,
    ) -> bool:
        """Return True if disk content has changed since last recorded
        hash for this (session_id, path) — i.e. an external editor
        touched the file behind agent's back. Useful for warning the
        agent before overwriting changes.
        """
        row = await self.get(session_id=session_id, path=path)
        if row is None:
            return False
        recorded = row.get("content_hash")
        if not recorded:
            return False
        return recorded != _content_hash(current_content)
