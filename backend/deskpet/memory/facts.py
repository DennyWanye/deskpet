"""Phase B — Structured fact storage with mem0-style merge semantics.

Architecture
------------
``messages`` keeps the raw chat log; ``facts`` stores **distilled
truths** extracted from those messages. One message can yield 0..N
facts; an existing fact can be ``replaced`` (e.g. "I switched to tea"
supersedes "I drink coffee"), ``merged`` (small fact added on top),
or ``no-op``'d (already known, lower confidence).

Two LLM calls per write (deferred and async):
  1. ``extract``  — "Does this message contain stable facts?" → JSON
     array of `{category, subject, key, value, confidence, evidence}`.
  2. ``merge``    — for each extracted fact that conflicts with an
     existing active fact, "is this new, replace, or no-op?" → decision.

Both can be skipped (returning empty / no-op) without breaking the
chat path. The extractor is invoked from a hook in
``SessionDB.on_message_written`` ONLY when the feature flag is on.

Tables (created by migration 009):

  facts(id, category, subject, key, value, confidence, source_msg_id,
        created_at, updated_at, evidence, is_active, decay_rate,
        last_recalled)

Categories:
  preference / profile / project / event / reflection

The default ``decay_rate`` is per-category — Phase E will diversify
them; for now we set a sensible default of 0.02 (same as P4 messages).
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Sequence

import aiosqlite

from deskpet.memory.memory_v2_schema import ensure_memory_v2_tables

log = logging.getLogger(__name__)


# Default per-category decay rate (1/day). Phase E will refine these.
_CATEGORY_DECAY: dict[str, float] = {
    "profile": 0.0,        # never decays — user name / birthday
    "preference": 0.005,   # 200d half-life-ish
    "project": 0.01,
    "event": 0.05,         # fast — yesterday's news
    "reflection": 0.02,
}

VALID_CATEGORIES = frozenset(_CATEGORY_DECAY.keys())


# Type aliases — strict to keep mocks trivial in tests.
LLMCall = Callable[[str], Awaitable[str]]


# Prompts. We pin them as module constants for testability + auditability.
_EXTRACT_PROMPT = """\
You are extracting stable facts from a single chat message.
Read the SOURCE below. If it contains any stable fact, preference,
profile field, or notable event the assistant should remember, output
a JSON array. Each entry MUST be a JSON object with keys:

  category  — one of: preference | profile | project | event
  subject   — who/what the fact is about (default: "user")
  key       — short snake_case key, e.g. "favorite_drink"
  value     — short free text value (< 100 chars)
  confidence — float 0..1, your certainty
  evidence  — the exact source phrase (verbatim, <= 80 chars)

If there is no fact worth remembering (small talk, tool noise, simple
acknowledgement), output an EMPTY JSON array: [].

Output ONLY the JSON array. No prose, no markdown fences.

SOURCE:
{content}

JSON:"""


_MERGE_PROMPT = """\
You are deciding how to integrate a new fact with an existing one.

NEW fact:
  category: {new_category}
  subject:  {new_subject}
  key:      {new_key}
  value:    {new_value}
  evidence: {new_evidence}

EXISTING fact (currently active):
  value:    {old_value}
  evidence: {old_evidence}
  updated:  {old_updated_ago_days} days ago

Choose ONE action. Output a JSON object with these fields exactly:

  {{"action": "replace", "value": "<new value to store>", "reason": "<one sentence>"}}
  → use when new info supersedes old (user changed their mind, profile field updated)

  {{"action": "merge", "value": "<combined value>", "reason": "..."}}
  → use when new info refines old (e.g. old said 'tea', new says 'oolong tea')

  {{"action": "no_op", "reason": "..."}}
  → use when new info is the same as old or weaker

Output ONLY the JSON object. No prose.

DECISION:"""


@dataclass
class ExtractedFact:
    category: str
    subject: str
    key: str
    value: str
    confidence: float
    evidence: str

    def normalize(self) -> "ExtractedFact":
        return ExtractedFact(
            category=self.category.strip().lower(),
            subject=(self.subject or "user").strip().lower(),
            key=self.key.strip().lower().replace(" ", "_"),
            value=self.value.strip(),
            confidence=max(0.0, min(1.0, float(self.confidence))),
            evidence=self.evidence.strip()[:200],
        )

    def is_valid(self) -> bool:
        return bool(
            self.category in VALID_CATEGORIES
            and self.subject
            and self.key
            and self.value
            and 0.0 <= self.confidence <= 1.0
        )


@dataclass
class MergeDecision:
    action: str   # 'replace' | 'merge' | 'no_op'
    value: Optional[str]
    reason: str


class FactsStore:
    """CRUD wrapper over the ``facts`` table.

    Strict separation: this class does NO LLM work — it just reads /
    writes rows. The LLM-driven extraction + merge lives in
    :class:`FactExtractor` below.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)

    async def _ensure_schema(self) -> None:
        """Lazy CREATE TABLE IF NOT EXISTS — runtime alternative to a
        migration. Memoised inside :mod:`memory_v2_schema` so subsequent
        calls are free.
        """
        await ensure_memory_v2_tables(self._db_path)

    async def upsert(
        self,
        *,
        category: str,
        subject: str,
        key: str,
        value: str,
        confidence: float,
        source_msg_id: Optional[int],
        evidence: str,
        decay_rate: Optional[float] = None,
    ) -> int:
        """Insert a brand-new active fact. Caller is expected to have
        already resolved conflicts with any existing (subject, key) via
        :class:`FactExtractor`.

        Returns the new ``facts.id``.
        """
        await self._ensure_schema()
        now = time.time()
        dr = decay_rate if decay_rate is not None else _CATEGORY_DECAY.get(
            category, 0.02
        )
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute("PRAGMA busy_timeout=5000")
            cur = await conn.execute(
                "INSERT INTO facts("
                "category, subject, key, value, confidence, source_msg_id, "
                "created_at, updated_at, evidence, is_active, decay_rate"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (
                    category, subject, key, value,
                    float(confidence), source_msg_id,
                    now, now, evidence, dr,
                ),
            )
            new_id = int(cur.lastrowid or 0)
            await cur.close()
            await conn.commit()
        return new_id

    async def deactivate(self, fact_id: int, *, now: Optional[float] = None) -> None:
        await self._ensure_schema()
        ts = now if now is not None else time.time()
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "UPDATE facts SET is_active = 0, updated_at = ? WHERE id = ?",
                (ts, int(fact_id)),
            )
            await conn.commit()

    async def update_value(
        self,
        fact_id: int,
        *,
        value: str,
        confidence: Optional[float] = None,
        evidence: Optional[str] = None,
        now: Optional[float] = None,
    ) -> None:
        await self._ensure_schema()
        ts = now if now is not None else time.time()
        sets = ["value = ?", "updated_at = ?"]
        params: list[Any] = [value, ts]
        if confidence is not None:
            sets.append("confidence = ?")
            params.append(float(confidence))
        if evidence is not None:
            sets.append("evidence = ?")
            params.append(evidence)
        params.append(int(fact_id))
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                f"UPDATE facts SET {', '.join(sets)} WHERE id = ?",
                tuple(params),
            )
            await conn.commit()

    async def find_active(
        self, *, subject: str, key: str
    ) -> Optional[dict[str, Any]]:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM facts "
                "WHERE subject = ? AND key = ? AND is_active = 1 "
                "ORDER BY updated_at DESC LIMIT 1",
                (subject, key),
            )
            row = await cur.fetchone()
            await cur.close()
        return dict(row) if row else None

    async def list_active(
        self,
        *,
        subject: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        await self._ensure_schema()
        where = ["is_active = 1"]
        params: list[Any] = []
        if subject:
            where.append("subject = ?")
            params.append(subject)
        if category:
            where.append("category = ?")
            params.append(category)
        params.append(limit)
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM facts WHERE " + " AND ".join(where)
                + " ORDER BY updated_at DESC LIMIT ?",
                tuple(params),
            )
            rows = await cur.fetchall()
            await cur.close()
        return [dict(r) for r in rows]

    async def search(
        self, query: str, *, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Plain LIKE-based search — used by :meth:`Retriever._facts_recall`.

        FTS on facts is overkill (table is tiny). Score = exact-substring
        match weight, ties broken by recency.
        """
        if not query or not query.strip():
            return []
        await self._ensure_schema()
        q = f"%{query.strip()}%"
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM facts "
                "WHERE is_active = 1 "
                "  AND (value LIKE ? OR evidence LIKE ? OR key LIKE ?) "
                "ORDER BY updated_at DESC LIMIT ?",
                (q, q, q, int(limit)),
            )
            rows = await cur.fetchall()
            await cur.close()
        return [dict(r) for r in rows]

    async def touch_recalled(self, fact_id: int, *, now: Optional[float] = None) -> None:
        await self._ensure_schema()
        ts = now if now is not None else time.time()
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "UPDATE facts SET last_recalled = ? WHERE id = ?",
                (ts, int(fact_id)),
            )
            await conn.commit()

    async def daily_decay(self, *, now: Optional[float] = None) -> int:
        """Apply per-fact ``confidence *= exp(-decay_rate * days_since_touch)``.

        Mirrors :func:`retriever.daily_decay` but operates on
        ``confidence`` instead of ``salience``. Returns the count of
        rows actually mutated (> 1e-6 delta).
        """
        await self._ensure_schema()
        ts = now if now is not None else time.time()
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                "SELECT id, confidence, COALESCE(last_recalled, updated_at), "
                "decay_rate FROM facts WHERE is_active = 1"
            )
            rows = await cur.fetchall()
            await cur.close()
            updates = []
            for fid, conf, last_touch, dr in rows:
                if dr <= 0:
                    continue
                days = max(0.0, (ts - float(last_touch)) / 86400.0)
                factor = math.exp(-float(dr) * days)
                new_conf = max(0.0, min(1.0, float(conf) * factor))
                if abs(new_conf - float(conf)) > 1e-6:
                    updates.append((new_conf, int(fid)))
            if not updates:
                return 0
            await conn.executemany(
                "UPDATE facts SET confidence = ? WHERE id = ?", updates
            )
            await conn.commit()
            return len(updates)


# ----------------------------------------------------------------------
# FactExtractor — orchestrates LLM-driven extract + merge
# ----------------------------------------------------------------------


class FactExtractor:
    """Asynchronous extractor: message → 0..N persisted ``facts`` rows.

    Designed to be invoked from ``SessionDB.on_message_written`` hook
    when ``config.memory.facts.enabled`` is true. Failure-isolation
    contract:
      * LLM error → log warning + return [] (chat path unaffected)
      * Parse error → skip that candidate (next ones still try)
      * DB error → log + re-raise (it's a real bug)
    """

    def __init__(
        self,
        store: FactsStore,
        *,
        extract_llm: LLMCall,
        merge_llm: Optional[LLMCall] = None,
    ) -> None:
        self._store = store
        self._extract_llm = extract_llm
        # Merge LLM can reuse extract_llm if the caller doesn't want a
        # separate provider.
        self._merge_llm = merge_llm or extract_llm

    async def process_message(
        self,
        *,
        message_id: int,
        content: str,
        role: str,
    ) -> list[dict[str, Any]]:
        """Top-level entry. Returns the list of persisted facts (newly
        inserted or updated). Empty list = no facts extracted or all
        no-op'd.
        """
        if role not in ("user", "assistant"):
            return []
        if not content or len(content.strip()) < 8:
            return []
        try:
            raw = await self._extract_llm(_EXTRACT_PROMPT.format(content=content[:2000]))
        except Exception as exc:  # noqa: BLE001
            log.warning("FactExtractor.extract LLM failed: %s", exc)
            return []
        extracted = _parse_extracted(raw)
        if not extracted:
            return []

        persisted: list[dict[str, Any]] = []
        for fact in extracted:
            fact = fact.normalize()
            if not fact.is_valid():
                log.debug("FactExtractor: dropping invalid fact %s", fact)
                continue
            existing = await self._store.find_active(
                subject=fact.subject, key=fact.key
            )
            if existing is None:
                fid = await self._store.upsert(
                    category=fact.category,
                    subject=fact.subject,
                    key=fact.key,
                    value=fact.value,
                    confidence=fact.confidence,
                    source_msg_id=message_id,
                    evidence=fact.evidence,
                )
                persisted.append({"id": fid, "action": "insert", **fact.__dict__})
                continue

            decision = await self._decide_merge(fact, existing)
            if decision.action == "no_op":
                continue
            if decision.action == "replace":
                # Deactivate old, insert new — keeps history.
                await self._store.deactivate(existing["id"])
                fid = await self._store.upsert(
                    category=fact.category,
                    subject=fact.subject,
                    key=fact.key,
                    value=decision.value or fact.value,
                    confidence=fact.confidence,
                    source_msg_id=message_id,
                    evidence=fact.evidence,
                )
                persisted.append({"id": fid, "action": "replace", **fact.__dict__})
            elif decision.action == "merge":
                # Update value in place, bump confidence by a small bit.
                merged_value = decision.value or f"{existing['value']}; {fact.value}"
                new_conf = max(
                    float(existing["confidence"]),
                    fact.confidence,
                )
                await self._store.update_value(
                    existing["id"],
                    value=merged_value,
                    confidence=new_conf,
                    evidence=fact.evidence,
                )
                persisted.append({
                    "id": existing["id"], "action": "merge", **fact.__dict__,
                    "value": merged_value,
                })
            else:
                log.debug("FactExtractor: unknown merge action %r", decision.action)
        return persisted

    async def _decide_merge(
        self, new: ExtractedFact, existing: dict[str, Any]
    ) -> MergeDecision:
        # Cheap rule: if values are exact-string-equal, no_op without
        # burning an LLM call.
        if new.value.strip() == str(existing.get("value", "")).strip():
            return MergeDecision(action="no_op", value=None, reason="exact match")

        old_ts = float(existing.get("updated_at") or 0.0)
        days_ago = max(0.0, (time.time() - old_ts) / 86400.0)
        prompt = _MERGE_PROMPT.format(
            new_category=new.category,
            new_subject=new.subject,
            new_key=new.key,
            new_value=new.value,
            new_evidence=new.evidence,
            old_value=existing.get("value"),
            old_evidence=existing.get("evidence") or "",
            old_updated_ago_days=f"{days_ago:.1f}",
        )
        try:
            raw = await self._merge_llm(prompt)
        except Exception as exc:  # noqa: BLE001
            log.warning("FactExtractor.merge LLM failed: %s", exc)
            return MergeDecision(
                action="no_op", value=None, reason=f"merge llm error: {exc}"
            )
        return _parse_merge_decision(raw)


# ----------------------------------------------------------------------
# Parsing helpers — defensive against LLM formatting drift
# ----------------------------------------------------------------------


def _parse_extracted(raw: str) -> list[ExtractedFact]:
    if not raw:
        return []
    text = raw.strip()
    if text.startswith("```"):
        nl = text.find("\n")
        if nl != -1:
            text = text[nl + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    lb, rb = text.find("["), text.rfind("]")
    if not (0 <= lb < rb):
        return []
    try:
        arr = json.loads(text[lb:rb + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(arr, list):
        return []
    out: list[ExtractedFact] = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        try:
            out.append(ExtractedFact(
                category=str(item.get("category", "")),
                subject=str(item.get("subject") or "user"),
                key=str(item.get("key", "")),
                value=str(item.get("value", "")),
                confidence=float(item.get("confidence", 0.5)),
                evidence=str(item.get("evidence", "")),
            ))
        except (TypeError, ValueError):
            continue
    return out


def _parse_merge_decision(raw: str) -> MergeDecision:
    if not raw:
        return MergeDecision(action="no_op", value=None, reason="empty llm response")
    text = raw.strip()
    if text.startswith("```"):
        nl = text.find("\n")
        if nl != -1:
            text = text[nl + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    lb, rb = text.find("{"), text.rfind("}")
    if not (0 <= lb < rb):
        return MergeDecision(action="no_op", value=None, reason="no json object")
    try:
        obj = json.loads(text[lb:rb + 1])
    except json.JSONDecodeError:
        return MergeDecision(action="no_op", value=None, reason="invalid json")
    action = str(obj.get("action", "no_op")).strip().lower()
    if action not in {"replace", "merge", "no_op"}:
        action = "no_op"
    return MergeDecision(
        action=action,
        value=obj.get("value"),
        reason=str(obj.get("reason", "")),
    )
