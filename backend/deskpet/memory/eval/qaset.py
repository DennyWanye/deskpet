# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""QA set builder — auto-generate (query, expected_msg_id) pairs.

Strategy
--------
We need a ground-truth set to measure recall quality. Hand-curating it
is expensive; instead we **reverse-engineer queries from existing
messages**:

  1. Pick a random sample of "memorable" messages — long-ish, low-tool,
     non-system entries. Both ``messages`` and ``messages_archive`` are
     valid sources (archive has 30+ day old data which makes for harder
     queries).
  2. For each picked message, ask a small LLM:
     "Suppose a user later asked the pet about this. Write 1-3 plausible
     short questions a user might type."
  3. Persist each ``(question, target_msg_id)`` pair into ``memory_qa_set``
     with ``source='llm_auto'``.

Failure modes
-------------
* LLM unreachable / quota / parse error → that message is skipped (we
  emit a debug log; the run still succeeds for the rest).
* No suitable source messages → build returns 0 inserted; CLI prints
  a hint about lowering the filter thresholds.

The "expected_msg_id" assumption is intentionally loose: a perfect
retriever might return a *semantically equivalent* but different message.
We compensate by computing MRR over a tolerant window (top_k=20) and
using the LLM-generated tags so partial credit is possible. See
``metrics.py`` for the scoring rules.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Sequence

import aiosqlite

from deskpet.memory.memory_v2_schema import ensure_memory_v2_tables

log = logging.getLogger(__name__)


# Type alias: an LLM callable that takes a single user prompt string
# and returns the assistant text. We deliberately keep this *narrower*
# than the full OpenAI message shape so tests can mock it with a
# trivial async lambda.
LLMCall = Callable[[str], Awaitable[str]]


# Minimum content length (chars) for a source message to be eligible.
# Below this the auto-generated query is rarely meaningful (one-word
# answers, "ok", etc.).
_MIN_SOURCE_LEN = 24

# How many questions we ask the LLM to generate per source. Keep at 2
# to balance coverage vs LLM cost.
_QUESTIONS_PER_SOURCE = 2

# The prompt template. We pin English instructions + ask the LLM to
# respond in the same language as the source — same convention as
# ``summarizer._SUMMARY_PROMPT``.
_PROMPT_TEMPLATE = (
    "You are generating training data for a recall-evaluation set.\n"
    "Below is one chat message between a user and a pet assistant.\n"
    "Write up to {n} short, natural-sounding questions a user might type\n"
    "LATER that should retrieve this exact message as the top answer.\n"
    "Questions must be self-contained (no 'remember when we...'),\n"
    "in the SAME LANGUAGE as the source message.\n"
    "Output ONLY a JSON array of strings. No prose. Example: [\"q1\", \"q2\"].\n\n"
    "SOURCE MESSAGE:\n{content}\n\n"
    "JSON array:"
)


@dataclass(frozen=True)
class QAItem:
    """One ground-truth pair."""

    query: str
    expected_msg_id: int
    tags: tuple[str, ...] = ()
    source: str = "llm_auto"
    notes: str = ""


class QASetBuilder:
    """Build / list / clear the ``memory_qa_set`` table.

    Parameters
    ----------
    db_path:
        Path to ``state.db``.
    llm_call:
        Async callable ``(prompt: str) -> str`` used to generate questions.
        Wrap whatever LLM provider you have; the function must NOT raise
        on transient failures — return ``""`` for skip semantics.
    rng_seed:
        Optional fixed seed so tests get deterministic source sampling.
    """

    def __init__(
        self,
        db_path: str | Path,
        llm_call: LLMCall,
        *,
        rng_seed: Optional[int] = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._llm = llm_call
        self._rng = random.Random(rng_seed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def build(
        self,
        *,
        max_items: int = 50,
        include_archive: bool = True,
        min_content_len: int = _MIN_SOURCE_LEN,
    ) -> list[QAItem]:
        """Generate up to ``max_items`` new QA items.

        Returns the list of inserted items (also persisted to DB). The
        ``id`` of each row is **not** in the dataclass — fetch via
        :meth:`list_all` if needed.

        Source selection is bounded by ``max_items / questions_per_source``
        candidate messages to keep LLM cost predictable.
        """
        await ensure_memory_v2_tables(self._db_path)
        target_sources = max(1, max_items // _QUESTIONS_PER_SOURCE + 1)
        sources = await self._pick_sources(
            target_sources,
            include_archive=include_archive,
            min_content_len=min_content_len,
        )
        if not sources:
            log.info("eval.qaset.build: no eligible sources (db=%s)", self._db_path)
            return []

        items: list[QAItem] = []
        for row in sources:
            if len(items) >= max_items:
                break
            try:
                qs = await self._questions_for(row["content"])
            except Exception as exc:  # noqa: BLE001 — single-source failure isolation
                log.debug("eval.qaset.build: LLM failed for msg_id=%s: %s",
                          row["id"], exc)
                continue
            for q in qs[:_QUESTIONS_PER_SOURCE]:
                if not q or not q.strip():
                    continue
                items.append(QAItem(
                    query=q.strip(),
                    expected_msg_id=int(row["id"]),
                    tags=tuple(self._tags_for(row)),
                    source="llm_auto",
                    notes="",
                ))
                if len(items) >= max_items:
                    break

        if items:
            await self._persist(items)
        return items

    async def add_manual(
        self,
        query: str,
        expected_msg_id: int,
        *,
        tags: Sequence[str] = (),
        notes: str = "",
    ) -> int:
        """Hand-curate one QA pair (e.g. from a user thumbs-down event)."""
        await ensure_memory_v2_tables(self._db_path)
        item = QAItem(
            query=query,
            expected_msg_id=expected_msg_id,
            tags=tuple(tags),
            source="manual",
            notes=notes,
        )
        ids = await self._persist([item])
        return ids[0]

    async def list_all(
        self, *, source: Optional[str] = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        await ensure_memory_v2_tables(self._db_path)
        async with aiosqlite.connect(self._db_path) as db:
            if source is not None:
                cur = await db.execute(
                    "SELECT id, source, query, expected_msg_id, tags, "
                    "created_at, notes FROM memory_qa_set "
                    "WHERE source = ? ORDER BY id LIMIT ?",
                    (source, limit),
                )
            else:
                cur = await db.execute(
                    "SELECT id, source, query, expected_msg_id, tags, "
                    "created_at, notes FROM memory_qa_set "
                    "ORDER BY id LIMIT ?",
                    (limit,),
                )
            rows = await cur.fetchall()
            await cur.close()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append({
                "id": int(r[0]),
                "source": r[1],
                "query": r[2],
                "expected_msg_id": int(r[3]),
                "tags": json.loads(r[4]) if r[4] else [],
                "created_at": float(r[5]),
                "notes": r[6] or "",
            })
        return out

    async def clear(self, *, source: Optional[str] = None) -> int:
        await ensure_memory_v2_tables(self._db_path)
        async with aiosqlite.connect(self._db_path) as db:
            if source is not None:
                cur = await db.execute(
                    "DELETE FROM memory_qa_set WHERE source = ?", (source,)
                )
            else:
                cur = await db.execute("DELETE FROM memory_qa_set")
            n = cur.rowcount or 0
            await cur.close()
            await db.commit()
        return n

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _pick_sources(
        self,
        n: int,
        *,
        include_archive: bool,
        min_content_len: int,
    ) -> list[dict[str, Any]]:
        """Random sample messages eligible to serve as QA sources.

        Selection rules:
          * role in ('user', 'assistant') — skip system + tool
          * char length >= min_content_len
          * tool_calls IS NULL (no agent tool-call results)
          * is_summary != 1 (the summary itself is fine, but as a target
            the recall test would be circular — summaries are derived
            artifacts)

        We pull a bounded over-sample (3x) then random.sample to avoid
        the SQL-side ``ORDER BY RANDOM()`` performance trap on big tables.
        """
        oversample = max(n * 3, 30)
        rows: list[dict[str, Any]] = []
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id, content, role, session_id FROM messages "
                "WHERE role IN ('user', 'assistant') "
                "  AND tool_calls IS NULL "
                "  AND COALESCE(is_summary, 0) != 1 "
                "  AND LENGTH(content) >= ? "
                "ORDER BY id DESC LIMIT ?",
                (min_content_len, oversample),
            )
            for r in await cur.fetchall():
                rows.append({
                    "id": r["id"],
                    "content": r["content"],
                    "role": r["role"],
                    "session_id": r["session_id"],
                    "from_archive": False,
                })
            await cur.close()
            if include_archive:
                cur = await db.execute(
                    "SELECT id, content, role, session_id FROM messages_archive "
                    "WHERE role IN ('user', 'assistant') "
                    "  AND tool_calls IS NULL "
                    "  AND LENGTH(content) >= ? "
                    "ORDER BY id DESC LIMIT ?",
                    (min_content_len, oversample),
                )
                for r in await cur.fetchall():
                    rows.append({
                        "id": r["id"],
                        "content": r["content"],
                        "role": r["role"],
                        "session_id": r["session_id"],
                        "from_archive": True,
                    })
                await cur.close()
        if not rows:
            return []
        # Random sample; cap at len(rows) when not enough candidates.
        k = min(n, len(rows))
        return self._rng.sample(rows, k)

    def _tags_for(self, row: dict[str, Any]) -> list[str]:
        tags: list[str] = []
        if row.get("from_archive"):
            tags.append("archived")
        if row.get("role") == "user":
            tags.append("user_utterance")
        else:
            tags.append("assistant_utterance")
        return tags

    async def _questions_for(self, content: str) -> list[str]:
        """Ask LLM for ``_QUESTIONS_PER_SOURCE`` candidate queries.

        Output must parse as JSON array. We tolerate ``[a, b]`` shape,
        ``["a", "b"]`` shape, and a single string fallback. Anything else
        → empty list (skip this source).
        """
        # Truncate very long source messages so the prompt stays small.
        snippet = content[:2000]
        prompt = _PROMPT_TEMPLATE.format(
            n=_QUESTIONS_PER_SOURCE, content=snippet
        )
        raw = await self._llm(prompt)
        return _parse_questions(raw)

    async def _persist(self, items: list[QAItem]) -> list[int]:
        ids: list[int] = []
        now = time.time()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA busy_timeout=5000")
            for it in items:
                cur = await db.execute(
                    "INSERT INTO memory_qa_set("
                    "source, query, expected_msg_id, tags, created_at, notes"
                    ") VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        it.source,
                        it.query,
                        it.expected_msg_id,
                        json.dumps(list(it.tags)) if it.tags else None,
                        now,
                        it.notes or None,
                    ),
                )
                ids.append(int(cur.lastrowid or 0))
                await cur.close()
            await db.commit()
        return ids


# ----------------------------------------------------------------------
# Output parser — defensive against LLM formatting drift
# ----------------------------------------------------------------------

def _parse_questions(raw: str) -> list[str]:
    """Best-effort JSON array → list[str] extractor.

    Accepts:
      * Plain ``["q1", "q2"]``
      * Code-fenced ``\`\`\`json\\n[...]``
      * Trailing prose after the array
      * Single-string fallback (entire raw becomes one question)

    Rejects anything that doesn't yield at least one non-empty string.
    """
    if not raw:
        return []
    text = raw.strip()
    # Strip ```json fences
    if text.startswith("```"):
        nl = text.find("\n")
        if nl != -1:
            text = text[nl + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    # Try to slice the first JSON array out of the response.
    lb, rb = text.find("["), text.rfind("]")
    if 0 <= lb < rb:
        try:
            arr = json.loads(text[lb:rb + 1])
            if isinstance(arr, list):
                return [str(x).strip() for x in arr if str(x).strip()]
        except json.JSONDecodeError:
            pass
    # Last-ditch: split by newline and accept lines that look like questions.
    lines = [
        ln.strip().lstrip("-*0123456789. ").strip("\"'")
        for ln in text.splitlines()
    ]
    return [ln for ln in lines if ln and "?" in ln][:_QUESTIONS_PER_SOURCE]
