"""Phase E — Daily reflection + procedural-memory skeleton.

This module provides two long-tail pieces:

1. :class:`ReflectionWorker` — runs once per day-ish (caller decides
   cadence). It inspects the last 24h of ``messages``, asks an LLM
   for a 1-3 sentence metacognitive note ("today the user mostly
   worked on X; pet noticed Y"), then persists it into ``facts`` with
   ``category='reflection'``. This single fact joins the L3 recall
   pool like any other and lets the pet "remember yesterday".

2. :class:`SkillMemoryStore` — CRUD over the ``skill_memory`` table.
   The Phase E minimum viable doesn't auto-extract skills from chat
   logs (that's a Phase F R&D question); it just provides the storage
   surface so users / agents can record "the X workflow" manually and
   the assembler can surface it next time a matching trigger fires.

Both pieces are **feature-flagged off by default**. They're entirely
additive — when nothing invokes them, the tables stay empty and
existing recall behaviour is unchanged.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Sequence

import aiosqlite

from deskpet.memory.facts import FactsStore
from deskpet.memory.memory_v2_schema import ensure_memory_v2_tables

log = logging.getLogger(__name__)


_LLMCall = Callable[[str], Awaitable[str]]


_REFLECTION_PROMPT = """\
You are the pet's reflective journal. Read the recent conversation
samples below (one block per turn, most recent first), then write
ONE short metacognitive note (1-2 sentences, same language as the
conversation) summarizing what the user did, was interested in, or
seemed to feel today. This note will be remembered tomorrow.

Be specific where possible (mention domains, projects, or feelings).
Avoid generic platitudes. If nothing notable happened, output a single
short observational note still — never refuse.

Output ONLY the note, no prefix.

RECENT TURNS:
{turns}

NOTE:"""


class ReflectionWorker:
    """Generate a daily reflection note and persist it as a fact.

    Failure-isolation: any error is caught and logged at debug level;
    no exception escapes ``run_once``. The chat path is never affected.
    """

    def __init__(
        self,
        db_path: str | Path,
        facts_store: FactsStore,
        llm_call: _LLMCall,
        *,
        window_hours: float = 24.0,
        max_turns: int = 12,
        subject: str = "user",
    ) -> None:
        self._db_path = Path(db_path)
        self._facts = facts_store
        self._llm = llm_call
        self._window_seconds = float(window_hours) * 3600.0
        self._max_turns = int(max_turns)
        self._subject = subject

    async def run_once(self, *, now: Optional[float] = None) -> Optional[int]:
        """Compose + persist one reflection. Returns the new ``facts.id``
        on success, ``None`` on skip / error.
        """
        await ensure_memory_v2_tables(self._db_path)
        ts_now = now if now is not None else time.time()
        cutoff = ts_now - self._window_seconds
        try:
            turns = await self._fetch_recent_turns(cutoff)
        except aiosqlite.Error as exc:
            log.debug("reflection: fetch turns failed: %s", exc)
            return None
        if not turns:
            log.debug("reflection: no turns in window, skipping")
            return None

        rendered = "\n\n".join(
            f"[{t['role']}] {t['content'][:240]}" for t in turns
        )
        prompt = _REFLECTION_PROMPT.format(turns=rendered)
        try:
            note = await self._llm(prompt)
        except Exception as exc:  # noqa: BLE001
            log.debug("reflection: LLM call failed: %s", exc)
            return None
        note = (note or "").strip()
        if not note:
            return None
        # Key includes the date so multiple days don't dedup down to one
        # active fact — each day's reflection stands alone, decaying
        # naturally per category rate.
        import datetime
        date_key = datetime.datetime.fromtimestamp(
            ts_now, tz=datetime.timezone.utc
        ).strftime("%Y_%m_%d")
        try:
            return await self._facts.upsert(
                category="reflection",
                subject=self._subject,
                key=f"daily_reflection_{date_key}",
                value=note,
                confidence=0.6,
                source_msg_id=None,
                evidence=f"window={int(self._window_seconds)}s",
            )
        except aiosqlite.Error as exc:
            log.debug("reflection: persist failed: %s", exc)
            return None

    async def _fetch_recent_turns(
        self, cutoff_ts: float
    ) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT role, content, created_at FROM messages "
                "WHERE role IN ('user', 'assistant') "
                "  AND created_at >= ? "
                "ORDER BY created_at DESC LIMIT ?",
                (cutoff_ts, self._max_turns),
            )
            rows = await cur.fetchall()
            await cur.close()
        return [dict(r) for r in rows]


# ----------------------------------------------------------------------
# SkillMemoryStore — procedural memory CRUD
# ----------------------------------------------------------------------


@dataclass
class SkillMemoryEntry:
    name: str
    description: str
    trigger_pattern: Optional[str]
    steps: list[str]


class SkillMemoryStore:
    """CRUD over ``skill_memory``. No auto-extraction in Phase E."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)

    async def add(self, entry: SkillMemoryEntry) -> int:
        await ensure_memory_v2_tables(self._db_path)
        now = time.time()
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                "INSERT INTO skill_memory("
                "name, description, trigger_pattern, steps_json, "
                "created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (
                    entry.name,
                    entry.description,
                    entry.trigger_pattern,
                    json.dumps(entry.steps),
                    now, now,
                ),
            )
            new_id = int(cur.lastrowid or 0)
            await cur.close()
            await conn.commit()
        return new_id

    async def list_all(self, *, limit: int = 100) -> list[dict[str, Any]]:
        await ensure_memory_v2_tables(self._db_path)
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM skill_memory ORDER BY usage_count DESC, updated_at DESC LIMIT ?",
                (int(limit),),
            )
            rows = await cur.fetchall()
            await cur.close()
        return [self._row_to_dict(r) for r in rows]

    async def find_by_name(self, name: str) -> Optional[dict[str, Any]]:
        await ensure_memory_v2_tables(self._db_path)
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM skill_memory WHERE name = ? LIMIT 1",
                (name,),
            )
            row = await cur.fetchone()
            await cur.close()
        return self._row_to_dict(row) if row else None

    async def recall(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        """LIKE-based search across name + description + trigger_pattern."""
        if not query or not query.strip():
            return []
        await ensure_memory_v2_tables(self._db_path)
        q = f"%{query.strip()}%"
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM skill_memory "
                "WHERE name LIKE ? OR description LIKE ? OR COALESCE(trigger_pattern, '') LIKE ? "
                "ORDER BY usage_count DESC, updated_at DESC LIMIT ?",
                (q, q, q, int(limit)),
            )
            rows = await cur.fetchall()
            await cur.close()
        return [self._row_to_dict(r) for r in rows]

    async def mark_used(self, skill_id: int, *, now: Optional[float] = None) -> None:
        await ensure_memory_v2_tables(self._db_path)
        ts = now if now is not None else time.time()
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "UPDATE skill_memory SET "
                "usage_count = usage_count + 1, last_used_at = ? "
                "WHERE id = ?",
                (ts, int(skill_id)),
            )
            await conn.commit()

    async def delete(self, skill_id: int) -> bool:
        await ensure_memory_v2_tables(self._db_path)
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                "DELETE FROM skill_memory WHERE id = ?", (int(skill_id),)
            )
            n = cur.rowcount or 0
            await cur.close()
            await conn.commit()
        return bool(n)

    def _row_to_dict(self, row: Any) -> Optional[dict[str, Any]]:
        if row is None:
            return None
        d = dict(row)
        try:
            d["steps"] = json.loads(d["steps_json"]) if d.get("steps_json") else []
        except json.JSONDecodeError:
            d["steps"] = []
        return d
