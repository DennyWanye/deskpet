"""SQLite-backed short-term conversation memory.

Uses aiosqlite for async access. Schema is append-only (no updates):
each turn is a row, `get_recent` pulls the last N ordered oldest-first
so callers can prepend them directly to the messages list.

Thread/task safety: each call opens a fresh connection — fine for the
single-process FastAPI app. If throughput becomes a concern, swap to a
connection pool; the Protocol contract won't change.

Schema is owned by ``memory.migrations/`` and applied lazily via
``memory.migrator.run_migrations`` on first use. This replaces the old
``_SCHEMA`` string constant so all DDL lives in one versioned place.
"""
from __future__ import annotations

import time
from pathlib import Path

import aiosqlite

from memory.base import ConversationTurn, SessionSummary, StoredTurn
from memory.migrator import run_migrations


class SqliteConversationMemory:
    """MemoryStore implementation backed by a single SQLite file."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False

    async def _ensure_schema(self) -> None:
        if self._initialized:
            return
        await run_migrations(self._db_path)
        self._initialized = True

    async def get_recent(
        self, session_id: str, limit: int = 10
    ) -> list[ConversationTurn]:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            # Pull last N by DESC, then reverse so caller gets chronological order.
            cursor = await db.execute(
                "SELECT role, content, created_at FROM conversation "
                "WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        turns = [ConversationTurn(role=r[0], content=r[1], created_at=r[2]) for r in rows]
        turns.reverse()
        return turns

    async def append(self, session_id: str, role: str, content: str) -> None:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO conversation (session_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                (session_id, role, content, time.time()),
            )
            await db.commit()

    async def clear(self, session_id: str) -> None:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "DELETE FROM conversation WHERE session_id = ?",
                (session_id,),
            )
            await db.commit()

    # ---------------- S14 management API ----------------
    # Not on the MemoryStore Protocol — these are admin-UI affordances.

    async def list_turns(
        self, session_id: str | None = None, limit: int | None = None
    ) -> list[StoredTurn]:
        """Return stored turns with their row ids.

        - ``session_id=None`` → across all sessions (for export).
        - ``limit=None`` → no upper bound; caller paginates UI-side.
        Rows are oldest → newest so the UI scrolls naturally.
        """
        await self._ensure_schema()
        where = "" if session_id is None else "WHERE session_id = ?"
        params: tuple = () if session_id is None else (session_id,)
        sql = (
            f"SELECT id, session_id, role, content, created_at "
            f"FROM conversation {where} ORDER BY created_at ASC, id ASC"
        )
        if limit is not None:
            sql += " LIMIT ?"
            params = params + (int(limit),)
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()
            await cursor.close()
        return [
            StoredTurn(
                id=row[0],
                session_id=row[1],
                role=row[2],
                content=row[3],
                created_at=row[4],
            )
            for row in rows
        ]

    async def delete_turn(self, turn_id: int) -> bool:
        """Remove a single turn by primary-key id. Returns ``True`` iff a row was deleted."""
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "DELETE FROM conversation WHERE id = ?", (int(turn_id),)
            )
            deleted = cursor.rowcount or 0
            await cursor.close()
            await db.commit()
        return deleted > 0

    async def list_sessions(self) -> list[SessionSummary]:
        """Summary row per session — count + latest timestamp."""
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT session_id, COUNT(*), MAX(created_at) "
                "FROM conversation GROUP BY session_id "
                "ORDER BY MAX(created_at) DESC"
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [
            SessionSummary(session_id=r[0], turn_count=r[1], last_message_at=r[2] or 0.0)
            for r in rows
        ]

    async def clear_all(self) -> int:
        """Wipe every conversation turn. Returns number of rows removed."""
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute("DELETE FROM conversation")
            removed = cursor.rowcount or 0
            await cursor.close()
            await db.commit()
        return removed
