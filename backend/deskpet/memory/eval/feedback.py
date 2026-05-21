"""Thumbs-up / thumbs-down feedback store.

Tiny CRUD wrapper over ``memory_user_feedback``. Consumed by:
  * Frontend AlertCenter / history bubble emitting ``memory_thumbs_up``
    or ``memory_thumbs_down`` ws messages.
  * Offline analysis: a high-negative-feedback ``source_msg_id`` is a
    signal that recall is surfacing the wrong thing for that query —
    candidate for re-tuning RRF weights or pruning the entry.

Spec / API surface deliberately tiny so the ws handler stays trivial.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from deskpet.memory.memory_v2_schema import ensure_memory_v2_tables


class FeedbackStore:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)

    async def record(
        self,
        *,
        source_msg_id: int,
        value: int,
        context_query: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> int:
        """Persist one feedback row. ``value`` MUST be -1 or +1."""
        if value not in (-1, 1):
            raise ValueError(f"value must be -1 or +1, got {value}")
        await ensure_memory_v2_tables(self._db_path)
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                "INSERT INTO memory_user_feedback("
                "source_msg_id, value, context_query, created_at, session_id"
                ") VALUES (?, ?, ?, ?, ?)",
                (
                    int(source_msg_id),
                    int(value),
                    context_query,
                    time.time(),
                    session_id,
                ),
            )
            new_id = int(cur.lastrowid or 0)
            await cur.close()
            await db.commit()
        return new_id

    async def summary(
        self, *, source_msg_id: Optional[int] = None
    ) -> dict[str, Any]:
        """Aggregate counts. If ``source_msg_id`` is given, scope to it."""
        await ensure_memory_v2_tables(self._db_path)
        async with aiosqlite.connect(self._db_path) as db:
            if source_msg_id is None:
                cur = await db.execute(
                    "SELECT value, COUNT(*) FROM memory_user_feedback "
                    "GROUP BY value"
                )
            else:
                cur = await db.execute(
                    "SELECT value, COUNT(*) FROM memory_user_feedback "
                    "WHERE source_msg_id = ? GROUP BY value",
                    (int(source_msg_id),),
                )
            rows = await cur.fetchall()
            await cur.close()
        counts = {int(r[0]): int(r[1]) for r in rows}
        up = counts.get(1, 0)
        down = counts.get(-1, 0)
        return {"up": up, "down": down, "net": up - down, "total": up + down}

    async def top_negative_messages(
        self, *, limit: int = 20, min_down: int = 1
    ) -> list[dict[str, Any]]:
        """Find messages with the most thumbs-down — recall trouble spots."""
        await ensure_memory_v2_tables(self._db_path)
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                "SELECT source_msg_id, "
                "SUM(CASE WHEN value=-1 THEN 1 ELSE 0 END) AS downs, "
                "SUM(CASE WHEN value=1 THEN 1 ELSE 0 END) AS ups "
                "FROM memory_user_feedback "
                "GROUP BY source_msg_id "
                "HAVING downs >= ? "
                "ORDER BY downs DESC, ups ASC LIMIT ?",
                (int(min_down), int(limit)),
            )
            rows = await cur.fetchall()
            await cur.close()
        return [
            {
                "source_msg_id": int(r[0]),
                "downs": int(r[1]),
                "ups": int(r[2]),
            }
            for r in rows
        ]
