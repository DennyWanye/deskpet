# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

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

import asyncio
import json
import logging
import math
import re
import time
import uuid
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
    # Stage 2 D13 v2 / R-MISS-3：episodic 固化通路（WI-S2.4）。
    # summary 抽出的 fact category；衰减慢（长期保留）。同时进
    # VALID_CATEGORIES 白名单（下方 frozenset 派生）。
    "episodic_summary": 0.01,
    # FP-4 WI-3.1：goal / decision / constraint 三类。
    # 半衰期参考：goal≈200d（ln2/0.005≈138d），decision≈1y，constraint≈最慢。
    "goal":       0.005,   # ≈200d，活跃目标长期留
    "decision":   0.002,   # ≈1 年（一次决策长期有效）
    "constraint": 0.001,   # 最慢（约束几乎不过期）
}

VALID_CATEGORIES = frozenset(_CATEGORY_DECAY.keys())


# Type aliases — strict to keep mocks trivial in tests.
LLMCall = Callable[[str], Awaitable[str]]


# Prompts. We pin them as module constants for testability + auditability.
#
# FP-4 WI-3.1 dual-version strategy:
#   _EXTRACT_PROMPT              — flag off (BC, 4 categories: preference/profile/project/event)
#   _EXTRACT_PROMPT_WITH_GOALS   — flag on  (7 categories: adds goal/decision/constraint)
#
# FactExtractor selects prompt via goal_facts ctor param (default False).
# Old version is BYTE-IDENTICAL to the original (backward-compatibility guard).

_EXTRACT_PROMPT = """\
You are extracting stable facts from a single chat message.
Read the SOURCE below. If it contains any stable fact, preference,
profile field, or notable event the assistant should remember, output
a JSON array. Each entry MUST be a JSON object with keys:

  category  — one of: preference | profile | project | event
  subject   — who/what the fact is about (default: "user")
  key       — short snake_case ENGLISH key, e.g. "favorite_drink"
  value     — short free text value (< 100 chars). MUST be in the SAME
              LANGUAGE as the source message (Chinese source → Chinese
              value; English source → English value). Do NOT translate.
  confidence — float 0..1, your certainty
  evidence  — the exact source phrase (verbatim, <= 80 chars)

Do NOT extract one-off, time-bound, or transient items — a single
appointment ("meeting tomorrow at 3"), a temporary file path mentioned
in passing, today's todo. Only extract DURABLE facts the assistant
should still know weeks later. When in doubt, leave it out.

If there is no fact worth remembering (small talk, tool noise, simple
acknowledgement, transient info), output an EMPTY JSON array: [].

Output ONLY the JSON array. No prose, no markdown fences.

SOURCE:
{content}

JSON:"""

# FP-4 WI-3.1 新版 prompt（flag on）：在 4 类基础上增加 goal / decision / constraint。
_EXTRACT_PROMPT_WITH_GOALS = """\
You are extracting stable facts from a single chat message.
Read the SOURCE below. If it contains any stable fact, preference,
profile field, or notable event the assistant should remember, output
a JSON array. Each entry MUST be a JSON object with keys:

  category  — one of: preference | profile | project | event | goal | decision | constraint
  subject   — who/what the fact is about (default: "user")
  key       — short snake_case ENGLISH key, e.g. "favorite_drink"
  value     — short free text value (< 100 chars). MUST be in the SAME
              LANGUAGE as the source message (Chinese source → Chinese
              value; English source → English value). Do NOT translate.
  confidence — float 0..1, your certainty
  evidence  — the exact source phrase (verbatim, <= 80 chars)

Do NOT extract one-off, time-bound, or transient items — a single
appointment ("meeting tomorrow at 3"), a temporary file path mentioned
in passing, today's todo. Only extract DURABLE facts the assistant
should still know weeks later. When in doubt, leave it out.

**例外（MUST 抽，不可遗漏）**：
- 持续性目标（"我想在月底前完成 X"、"打算学完这门课"）→ category=goal，value 建议带 [active] 前缀。
- 已做的决策（"这个项目决定用 TypeScript"、"选了方案 A"）→ category=decision。
- 长期约束（"只能用开源库"、"预算 2000 以内"）→ category=constraint。
区分关键：一次性事件（明天 3 点开会）仍排除；带方向性/长期有效的意图与约束必须抽出。

If there is no fact worth remembering (small talk, tool noise, simple
acknowledgement, transient info), output an EMPTY JSON array: [].

Output ONLY the JSON array. No prose, no markdown fences.

SOURCE:
{content}

JSON:"""


_CROSS_KEY_CONFLICT_PROMPT = """\
You are detecting **cross-key contradictions** among existing facts.

NEW fact (about to be inserted):
  category: {new_category}
  subject:  {new_subject}
  key:      {new_key}
  value:    {new_value}
  evidence: {new_evidence}    # the user's original phrasing — read this
                              # carefully; it often contains the explicit
                              # correction signal ("其实不是 X，是 Y" /
                              # "actually I was wrong about X")

EXISTING active facts on the same subject (id / key / value /
updated_ago_days). They are candidates that MIGHT contradict the new
fact even though their key differs:
{candidates}

Decision logic:
  * If the NEW fact's **evidence** contains an explicit correction
    ("其实不是 X，是 Y" / "搞错了" / "actually I was wrong about X" /
    "X 是错的，正确的是 Y" / "我刚才说 X 是错的" / 等) AND an EXISTING
    fact's value or key matches the thing being corrected (X) → the
    existing fact is OBSOLETE; mark it superseded.
  * If the NEW fact simply adds another data point in the same broad
    domain (e.g. "I'm also allergic to shrimp" added to peanut allergy
    with NO explicit correction) → NOT a contradiction; keep both.
  * Different domains (coffee preference vs hiking hobby) → never a
    contradiction.

Output ONLY a single JSON object:
  {{"conflicts": [{{"old_id": <int>, "reason": "<one sentence>"}}, ...],
    "should_insert": true|false}}

If unsure → conflicts: [] and should_insert: true (safe default).

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
    scope: str = "user"  # FP-4 WI-3.1: user | session

    def normalize(self) -> "ExtractedFact":
        return ExtractedFact(
            category=self.category.strip().lower(),
            subject=(self.subject or "user").strip().lower(),
            key=self.key.strip().lower().replace(" ", "_"),
            value=self.value.strip(),
            confidence=max(0.0, min(1.0, float(self.confidence))),
            evidence=self.evidence.strip()[:200],
            scope=(self.scope or "user").strip().lower(),
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


@dataclass
class CrossKeyDecision:
    """Stage 2 WI-S2.1a：cross-key 矛盾扫描的 LLM 输出。

    ``conflicts`` 每项 dict 形如 ``{"old_id": int, "reason": str}``。
    LLM 给出非法形态（缺 old_id / 非 int）由调用方过滤（TS1-13）。
    """
    should_insert: bool
    conflicts: list[dict[str, Any]] = field(default_factory=list)


class FactsStore:
    """CRUD wrapper over the ``facts`` table.

    Strict separation: this class does NO LLM work — it just reads /
    writes rows. The LLM-driven extraction + merge lives in
    :class:`FactExtractor` below.
    """

    def __init__(self, db_path: str | Path, *, embedder: Any | None = None) -> None:
        self._db_path = Path(db_path)
        # 记忆系统升级 WI-M1.4 / PRD §3.1：写 fact 时对规范文本
        # "key: value" 调 embedder 存进 embedding 列，召回端走向量。
        # None / mock embedder → 不写向量，召回端降级 LIKE 兜底。
        self._embedder = embedder

    async def _embed_fact(self, key: str, value: str) -> Optional[bytes]:
        """规范文本 "key: value" → float32 向量 bytes。

        无 embedder / mock embedder / 编码失败 → 返回 None（embedding 列
        留空，召回端降级到 LIKE 子串兜底）。
        """
        emb = self._embedder
        if emb is None:
            return None
        try:
            if hasattr(emb, "is_mock") and emb.is_mock():
                return None
            arr = await emb.encode([f"{key}: {value}"])
        except Exception as exc:  # noqa: BLE001
            # embedding 算不出 → 该 fact embedding 列留 NULL，向量召回永远
            # 跳过它（facts 无 backfill，比 messages 更糟）。warning 让"fact
            # 静默退化成只能 LIKE 召回"可见（审计 FATAL-B / hunter FATAL-3）。
            log.warning("FactsStore embed failed (embedding left NULL): %s", exc)
            return None
        try:
            import numpy as _np

            if arr is None or len(arr) == 0:
                return None
            return _np.asarray(arr[0], dtype=_np.float32).tobytes()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "FactsStore embed serialize failed (embedding left NULL): %s",
                exc,
            )
            return None

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
        scope: str = "user",  # FP-4 WI-3.1: user | session
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
        embedding = await self._embed_fact(key, value)  # WI-M1.4
        # FP-4 WI-3.1: write scope column when available; fall back gracefully
        # if column not yet ALTERed (old DB without scope column).
        try:
            async with aiosqlite.connect(self._db_path) as conn:
                await conn.execute("PRAGMA busy_timeout=5000")
                cur = await conn.execute(
                    "INSERT INTO facts("
                    "category, subject, key, value, confidence, source_msg_id, "
                    "created_at, updated_at, evidence, is_active, decay_rate, "
                    "embedding, scope"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                    (
                        category, subject, key, value,
                        float(confidence), source_msg_id,
                        now, now, evidence, dr, embedding, scope,
                    ),
                )
                new_id = int(cur.lastrowid or 0)
                await cur.close()
                await conn.commit()
        except Exception as exc:
            # scope column missing (old DB without ALTER) → retry without scope
            if "scope" in str(exc).lower() or "table facts has no column" in str(exc).lower():
                log.debug("upsert: scope column unavailable, falling back: %s", exc)
                async with aiosqlite.connect(self._db_path) as conn:
                    await conn.execute("PRAGMA busy_timeout=5000")
                    cur = await conn.execute(
                        "INSERT INTO facts("
                        "category, subject, key, value, confidence, source_msg_id, "
                        "created_at, updated_at, evidence, is_active, decay_rate, "
                        "embedding"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                        (
                            category, subject, key, value,
                            float(confidence), source_msg_id,
                            now, now, evidence, dr, embedding,
                        ),
                    )
                    new_id = int(cur.lastrowid or 0)
                    await cur.close()
                    await conn.commit()
            else:
                raise
        return new_id

    async def upsert_replacing(self, **kwargs: Any) -> int:
        """Upsert with same-(subject,key) replacement (FP-4 TC-4.5 修复).

        ``upsert`` 是纯 INSERT(冲突消解归 FactExtractor);但直连调用方
        (如 B-10 goal→facts 钩)没有 extractor 兜底 → 每次写堆一行,真机
        15 行全 active。本方法组合 find_active → upsert → mark_superseded,
        保证同 key 始终只有 1 条 active,旧行走 supersede 链保历史
        (对齐 mem0/Zep 软失效语义,冻结契约 §1.7)。
        """
        # 取全部 active 同 key 行(不止最新一条)——修复前堆积的脏数据
        # (真机 15 行全 active)也要在下一次写入时自愈;只 supersede 最新
        # 一条会让旧堆积永远留 active(TC-4.5 复验 2026-06-11 发现)。
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                "SELECT id FROM facts "
                "WHERE subject = ? AND key = ? AND is_active = 1",
                (kwargs["subject"], kwargs["key"]),
            )
            old_ids = [int(r[0]) for r in await cur.fetchall()]
            await cur.close()
        new_id = await self.upsert(**kwargs)
        for old_id in old_ids:
            if old_id != new_id:
                await self.mark_superseded(old_id=old_id, superseded_by=new_id)
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
        key: Optional[str] = None,
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
        # WI-M1.4：value 变了 → 向量过期，传了 key 就重算 embedding。
        if key is not None:
            embedding = await self._embed_fact(key, value)
            if embedding is not None:
                sets.append("embedding = ?")
                params.append(embedding)
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

    async def get_by_id(self, fact_id: int) -> Optional[dict[str, Any]]:
        """Fetch a fact by integer ID. Returns None when absent.

        WI-T3.1 v3 R-MISS-9：memory_read tool 需要按 ID 查 — 原 facts.py 仅有
        find_active(subject,key)/list_active 等过滤接口，无 by-id 主键查。
        """
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM facts WHERE id = ?",
                (int(fact_id),),
            )
            row = await cur.fetchone()
            await cur.close()
        return dict(row) if row else None

    async def list_active(
        self,
        *,
        subject: Optional[str] = None,
        category: Optional[str] = None,
        scope: Optional[str] = None,  # FP-4 WI-3.1: filter by scope (user|session)
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
        if scope is not None:
            where.append("scope = ?")
            params.append(scope)
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
        """Tokenized LIKE search — used by :meth:`Retriever._facts_recall`.

        F5 修复（2026-06-01）：旧实现 `LIKE '%{整个 query}%'` 要求字段含整个
        query 串，自然语言 query 几乎永远 0 命中。改为 query 分词后逐词
        OR LIKE（value / evidence / key），按**命中词数**降序、recency 次之。

        FTS on facts is overkill (table is tiny)。分词 LIKE 只修"词在但整串
        不匹配"；纯语义 / 跨语言（"宠物"↔"橘猫"）仍须走 vector_search，
        调用方在 embedder 可用时应优先向量路、LIKE 仅兜底。

        分词为空（如 query 全是停用词/单字）→ 退回整串 LIKE 作保底。
        """
        if not query or not query.strip():
            return []
        await self._ensure_schema()
        from deskpet.memory.text_tokenize import tokenize_query

        tokens = tokenize_query(query)
        if not tokens:
            tokens = [query.strip()]  # 分词空 → 整串保底

        # 每个 token 一组 (value/evidence/key) LIKE；命中任一 token 即入选，
        # 命中词数 = sum(各 token 是否在三字段任一出现)，越多越相关。
        # SQL 里用 CASE 累加每个 token 的命中标志当 match_score。
        score_terms = []
        where_terms = []
        params: list[Any] = []
        for t in tokens:
            pat = f"%{t}%"
            # match flag: 该 token 命中三字段任一 → +1
            score_terms.append(
                "(CASE WHEN (value LIKE ? OR evidence LIKE ? OR key LIKE ?) "
                "THEN 1 ELSE 0 END)"
            )
            params.extend([pat, pat, pat])
            where_terms.append("(value LIKE ? OR evidence LIKE ? OR key LIKE ?)")
            params.extend([pat, pat, pat])
        match_score = " + ".join(score_terms)
        where_or = " OR ".join(where_terms)
        sql = (
            f"SELECT *, ({match_score}) AS _match_score FROM facts "
            f"WHERE is_active = 1 AND ({where_or}) "
            "ORDER BY _match_score DESC, updated_at DESC LIMIT ?"
        )
        params.append(int(limit))
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(sql, tuple(params))
            rows = await cur.fetchall()
            await cur.close()
        # 去掉内部 _match_score 字段再返回（保持原契约）
        out = []
        for r in rows:
            d = dict(r)
            d.pop("_match_score", None)
            out.append(d)
        return out

    async def find_by_entities(
        self,
        entities: list[str],
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Stage 2 WI-S2.2 / PRD §3.3：实体精准索引（LIKE 只查 value 列）。

        把 query 端 NER 抽出的实体列表喂进来，逐个对 ``facts.value`` 做
        ``LIKE '%entity%'`` —— **只查 value 列**（不查 subject / key），原因：

          * ``subject`` 多为 ``"user"`` 等通用值，LIKE 噪声极大。
          * ``key`` 是英文 snake_case 标识符（``pet_name`` / ``favorite_drink``），
            与中文实体严重不匹配，召不到也徒增 SQL 成本。

        规则：

          * 实体列表上限 5 个（超过截断，对应 PRD §3.3 ``entities[:5]``）。
          * 单实体长度 < 2 字符跳过（中文单字 / 英文单字母无意义）。
          * 按 ``id`` 去重 —— 多个实体同时命中同一 fact 只算一次。
          * 按 ``updated_at`` 降序排（最近更新的事实优先）。

        ``vector_search`` 之外的第二条 facts 召回路径。EnhancedRetriever 把
        结果按 ``entity_weight``（默认 0.10）折叠进 RRF。空 entities → 空返；不抛错。
        """
        if not entities:
            return []
        await self._ensure_schema()
        seen: dict[int, dict[str, Any]] = {}
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            for e in entities[:5]:  # 上限 5 个实体
                if not isinstance(e, str) or len(e.strip()) < 2:
                    continue
                pat = f"%{e.strip()}%"
                cur = await conn.execute(
                    "SELECT * FROM facts "
                    "WHERE is_active = 1 AND value LIKE ? "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (pat, int(limit)),
                )
                for r in await cur.fetchall():
                    seen.setdefault(int(r["id"]), dict(r))
                await cur.close()
        rows = list(seen.values())
        rows.sort(key=lambda r: -float(r.get("updated_at") or 0))
        return rows[: int(limit)]

    async def vector_search(
        self, query_embedding: Any, *, limit: int = 10
    ) -> list[dict[str, Any]]:
        """记忆系统升级 WI-M1.4 / PRD §3.1：facts 向量召回。

        对所有 active 且有 ``embedding`` 的 fact 做 Python brute-force
        cosine，返回 top_k（每行附 ``_score`` cosine 相似度）。facts 表
        小（百行级），单次召回 <1ms，无需向量索引。

        ``query_embedding`` 为 list[float] / np.ndarray。embedder 处于
        mock 模式时 facts 行没写 embedding —— 这些行被跳过，调用方
        （EnhancedRetriever._collect_fact_hits）应据空结果降级 LIKE。
        """
        if query_embedding is None:
            return []
        await self._ensure_schema()
        import numpy as np

        q = np.asarray(query_embedding, dtype=np.float32).ravel()
        q_norm = float(np.linalg.norm(q))
        if q_norm == 0.0:
            return []
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM facts "
                "WHERE is_active = 1 AND embedding IS NOT NULL"
            )
            rows = await cur.fetchall()
            await cur.close()
        scored: list[tuple[float, dict[str, Any]]] = []
        for r in rows:
            blob = r["embedding"]
            if not blob:
                continue
            vec = np.frombuffer(blob, dtype=np.float32)
            if vec.shape != q.shape:
                continue
            v_norm = float(np.linalg.norm(vec))
            if v_norm == 0.0:
                continue
            cos = float(np.dot(q, vec) / (q_norm * v_norm))
            d = dict(r)
            d["_score"] = cos
            scored.append((cos, d))
        scored.sort(key=lambda t: -t[0])
        return [d for _, d in scored[: int(limit)]]

    async def touch_recalled(self, fact_id: int, *, now: Optional[float] = None) -> None:
        await self._ensure_schema()
        ts = now if now is not None else time.time()
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "UPDATE facts SET last_recalled = ? WHERE id = ?",
                (ts, int(fact_id)),
            )
            await conn.commit()

    # ------------------------------------------------------------------
    # Stage 2 / WI-S2.1a — cross-key 矛盾治理 + memory_forget 配套
    # ------------------------------------------------------------------

    async def mark_superseded(
        self, *, old_id: int, superseded_by: int,
    ) -> None:
        """A1 v2 D3：原子标 old_id 为 inactive 且记录被谁推翻。

        ``WHERE is_active=1`` 守护：若 old_id 已 inactive（被另一并发
        路径先标过），本次 UPDATE 静默 0 行，不报错（TS1-10）。
        """
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.execute(
                "UPDATE facts SET is_active = 0, superseded_by = ? "
                "WHERE id = ? AND is_active = 1",
                (int(superseded_by), int(old_id)),
            )
            await conn.commit()

    async def mark_forgotten(
        self, fact_id: int, *, op_id: str, ts: Optional[float] = None,
    ) -> None:
        """memory_forget 标 inactive + 记 forgotten_at（D5 v2）。

        evidence 末尾追加 ``[forget_op:<op_id>]`` 标记，restore_from_undo
        靠这个反查。``WHERE is_active=1`` 守护：已 inactive / 不存在
        ID 调用静默无效（TS2-2）。
        """
        await self._ensure_schema()
        when = ts if ts is not None else time.time()
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.execute(
                "UPDATE facts SET is_active = 0, forgotten_at = ? "
                "WHERE id = ? AND is_active = 1",
                (float(when), int(fact_id)),
            )
            await conn.execute(
                "UPDATE facts SET evidence = COALESCE(evidence, '') || ? "
                "WHERE id = ?",
                (f"\n[forget_op:{op_id}]", int(fact_id)),
            )
            await conn.commit()

    async def restore_from_undo(
        self, op_id: str, *, max_age_seconds: float = 5.0,
    ) -> list[int]:
        """5 秒 undo 窗口内 restore；超时返回 [] (D5 v2)。

        现在 - forgotten_at < max_age_seconds 且 evidence 含
        ``[forget_op:<op_id>]`` 的行被恢复 is_active=1 / forgotten_at=NULL。
        清理 evidence 末尾的 marker 不必（多一行噪声但不影响逻辑）。
        """
        await self._ensure_schema()
        now = time.time()
        cutoff = now - float(max_age_seconds)
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute("PRAGMA busy_timeout=5000")
            cur = await conn.execute(
                "SELECT id FROM facts "
                "WHERE forgotten_at IS NOT NULL AND forgotten_at >= ? "
                "AND evidence LIKE ?",
                (cutoff, f"%[forget_op:{op_id}]%"),
            )
            ids = [int(r[0]) for r in await cur.fetchall()]
            await cur.close()
            if not ids:
                return []
            await conn.executemany(
                "UPDATE facts SET is_active = 1, forgotten_at = NULL "
                "WHERE id = ?",
                [(fid,) for fid in ids],
            )
            await conn.commit()
        return ids

    async def is_forgotten_recently(
        self, *, subject: str, key: str, within_days: int = 7,
    ) -> bool:
        """R-MISS-2 v2：subject/key 最近 N 天被 forgotten 过则返 True。

        FactExtractor 写入前查它防"用户喊忘记 → 一分钟后又被自动重新
        抽出来覆盖遗忘"。within_days 默认 7 天。
        """
        await self._ensure_schema()
        cutoff = time.time() - float(within_days) * 86400.0
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM facts "
                "WHERE subject = ? AND key = ? "
                "AND forgotten_at IS NOT NULL AND forgotten_at >= ? "
                "LIMIT 1",
                (subject, key, cutoff),
            )
            row = await cur.fetchone()
            await cur.close()
        return row is not None

    async def list_superseded_chain(
        self, fact_id: int, *, max_depth: int = 16,
    ) -> list[dict[str, Any]]:
        """从 fact_id 起向前追 ``superseded_by`` 链，返新→旧的 facts。

        TS1-11：3 级链返 3 条。链中含 ID 不存在 → 静默断；防 max_depth
        无限循环。
        """
        await self._ensure_schema()
        out: list[dict[str, Any]] = []
        seen: set[int] = set()
        cur_id: Optional[int] = int(fact_id)
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            depth = 0
            while cur_id is not None and depth < max_depth:
                if cur_id in seen:
                    break
                seen.add(cur_id)
                c = await conn.execute(
                    "SELECT * FROM facts WHERE id = ?", (int(cur_id),)
                )
                row = await c.fetchone()
                await c.close()
                if row is None:
                    break
                out.append(dict(row))
                nxt = row["superseded_by"]
                cur_id = int(nxt) if nxt is not None else None
                depth += 1
        return out

    async def vector_search_in_subject(
        self, query_embedding: Any, *, subject: str, limit: int = 10,
    ) -> list[dict[str, Any]]:
        """D3 v2 混合视野：限 same subject 的向量召回。

        与 vector_search 同语义，多加 WHERE subject=? 把语义最近 N 条
        限定在同一 subject 范围（防把别 subject 的 fact 混进 LLM 视野
        造成误判）。
        """
        if query_embedding is None or not subject:
            return []
        await self._ensure_schema()
        import numpy as np

        q = np.asarray(query_embedding, dtype=np.float32).ravel()
        q_norm = float(np.linalg.norm(q))
        if q_norm == 0.0:
            return []
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM facts "
                "WHERE is_active = 1 AND subject = ? AND embedding IS NOT NULL",
                (subject,),
            )
            rows = await cur.fetchall()
            await cur.close()
        scored: list[tuple[float, dict[str, Any]]] = []
        for r in rows:
            blob = r["embedding"]
            if not blob:
                continue
            vec = np.frombuffer(blob, dtype=np.float32)
            if vec.shape != q.shape:
                continue
            v_norm = float(np.linalg.norm(vec))
            if v_norm == 0.0:
                continue
            cos = float(np.dot(q, vec) / (q_norm * v_norm))
            d = dict(r)
            d["_score"] = cos
            scored.append((cos, d))
        scored.sort(key=lambda t: -t[0])
        return [d for _, d in scored[: int(limit)]]

    async def set_pinned(self, fact_id: int, pinned: bool) -> None:
        """Pin or unpin a fact — pinned facts skip daily_decay entirely.

        Mirrors ``mark_forgotten`` pattern: WHERE is_active=1 guard omitted
        (pin/unpin should work regardless of active state, per WI-3.3 spec).
        Missing fact_id → 0 rows updated, silent (no error).
        """
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.execute(
                "UPDATE facts SET pinned = ? WHERE id = ?",
                (1 if pinned else 0, int(fact_id)),
            )
            await conn.commit()

    async def daily_decay(self, *, now: Optional[float] = None) -> int:
        """Apply per-fact ``confidence *= exp(-decay_rate * days_since_touch)``.

        Mirrors :func:`retriever.daily_decay` but operates on
        ``confidence`` instead of ``salience``. Returns the count of
        rows actually mutated (> 1e-6 delta).

        WI-3.3: pinned facts (``pinned=1``) skip decay entirely — their
        confidence never drops. The SQL filter ``AND pinned=0`` handles this
        cheaply (no Python-level skip needed). Guard: if the ``pinned`` column
        is absent (old DB where ALTER failed), falls back to the original
        ``WHERE is_active=1`` query so startup is not blocked.
        """
        await self._ensure_schema()
        ts = now if now is not None else time.time()
        async with aiosqlite.connect(self._db_path) as conn:
            # WI-3.3: try the pinned-aware query first; fall back if column absent.
            _pinned_sql = (
                "SELECT id, confidence, COALESCE(last_recalled, updated_at), "
                "decay_rate FROM facts WHERE is_active = 1 AND pinned = 0"
            )
            _fallback_sql = (
                "SELECT id, confidence, COALESCE(last_recalled, updated_at), "
                "decay_rate FROM facts WHERE is_active = 1"
            )
            try:
                cur = await conn.execute(_pinned_sql)
            except Exception:  # noqa: BLE001 — OperationalError: no column pinned
                cur = await conn.execute(_fallback_sql)
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

    记忆系统升级 WI-M1.2: 由 main.py 的 ``_on_message_written`` 组合
    fanout callable 在 ``config.memory.v2.facts_extract`` 为真时通过
    ``asyncio.create_task`` 异步调起（shadow 模式：只写 ``facts`` 表，
    不进召回）。Failure-isolation contract:
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
        min_chars: int = 8,
        cross_key_merge: bool = False,
        cross_key_llm: Optional[LLMCall] = None,
        embedder: Any | None = None,
        forget_within_days: int = 7,
        goal_facts: bool = False,  # FP-4 WI-3.1: enable goal/decision/constraint extraction
    ) -> None:
        self._store = store
        self._extract_llm = extract_llm
        # Merge LLM can reuse extract_llm if the caller doesn't want a
        # separate provider.
        self._merge_llm = merge_llm or extract_llm
        # FP-4 WI-3.1: select prompt version based on flag
        self._goal_facts = bool(goal_facts)
        # 记忆系统升级 WI-M1.2 / D4：字数采样门由 config
        # [memory.v2.facts].min_user_chars 驱动，取代旧的硬编码 `< 8`。
        self._min_chars = int(min_chars)
        # 记忆系统升级 WI-M1.2 / 评审缺口 2：facts 抽取由 fanout 经
        # asyncio.create_task 异步并发调起 —— 同 (subject,key) 的两条消息
        # 并发抽取时，两个 task 都可能 find_active=None 各插一行 → 重复
        # active。用一把锁把 find→merge→upsert 的 DB 临界区串行化（LLM
        # extract 仍可并发，锁只罩持久化阶段）。
        self._persist_lock = asyncio.Lock()
        # Stage 2 / WI-S2.1a — cross-key 矛盾治理（flag 关时整段跳过，
        # _persist_extracted 行为与 Stage 1 字节级一致）。
        self._cross_key_merge_enabled = bool(cross_key_merge)
        self._cross_key_llm = cross_key_llm or self._merge_llm
        # D3 v2 混合视野的语义部分需要 embedder；无 embedder 时只用
        # "最近 20 条"（仍能跑，覆盖率降）。
        self._embedder = embedder
        # R-MISS-2：被 forgotten 的 subject/key 在 N 天内不重新插。
        self._forget_within_days = int(forget_within_days)

    async def process_message(
        self,
        *,
        message_id: int,
        content: str,
        role: str,
        source: str = "user_message",
    ) -> list[dict[str, Any]]:
        """Top-level entry. Returns the list of persisted facts (newly
        inserted or updated). Empty list = no facts extracted or all
        no-op'd.

        Stage 2 / WI-S2.4 (D13 v2)：``source`` 参数 + 联合白名单。
          * role=user/assistant → 默认允许（不论 source）
          * role=system AND source="summarizer" → 允许（episodic→semantic）
          * 其他组合 → 拒（return []）

        ``source="summarizer"`` 抽出的 fact category 全被强制改写为
        ``"episodic_summary"`` —— 保证 summary 二次抽取的 fact 与 user
        直说的 fact 在统计上可分离。
        """
        # Stage 2 D13 v2：联合白名单
        if not (
            role in ("user", "assistant")
            or (role == "system" and source == "summarizer")
        ):
            return []
        if not content or len(content.strip()) < self._min_chars:
            return []
        # FP-4 WI-3.1: select prompt based on goal_facts flag
        _prompt_tmpl = _EXTRACT_PROMPT_WITH_GOALS if self._goal_facts else _EXTRACT_PROMPT
        try:
            raw = await self._extract_llm(_prompt_tmpl.format(content=content[:2000]))
        except Exception as exc:  # noqa: BLE001
            log.warning("FactExtractor.extract LLM failed: %s", exc)
            return []
        extracted = _parse_extracted(raw)
        if not extracted:
            return []

        # Stage 2 D13 v2：summarizer 来源 → category override
        if source == "summarizer":
            for f in extracted:
                f.category = "episodic_summary"

        # 持久化阶段串行化（评审缺口 2）—— LLM extract 已并发跑完，这里
        # 锁住 find→merge→upsert 防同 (subject,key) 并发双插。
        async with self._persist_lock:
            return await self._persist_extracted(extracted, message_id)

    async def _persist_extracted(
        self,
        extracted: list["ExtractedFact"],
        message_id: int,
    ) -> list[dict[str, Any]]:
        """find → merge-decide → upsert 每条抽取出的 fact。

        Stage 2 / WI-S2.1a：在 find_active=None（新 key）路径上额外做
        cross-key 矛盾扫描 —— 把 same-subject 的"最近 20 + 语义最近 10"
        喂给 LLM，命中矛盾则 mark_superseded 旧 fact。

        调用方必须已持有 ``self._persist_lock`` —— 见 process_message。
        """
        persisted: list[dict[str, Any]] = []
        for fact in extracted:
            fact = fact.normalize()
            if not fact.is_valid():
                log.debug("FactExtractor: dropping invalid fact %s", fact)
                continue
            # R-MISS-2：被 forgotten 的 (subject, key) 在 N 天内不重新插。
            try:
                if await self._store.is_forgotten_recently(
                    subject=fact.subject,
                    key=fact.key,
                    within_days=self._forget_within_days,
                ):
                    log.debug(
                        "Skip fact (subject=%s key=%s): forgotten within %d days",
                        fact.subject, fact.key, self._forget_within_days,
                    )
                    continue
            except Exception as exc:  # noqa: BLE001
                # is_forgotten_recently 走 forgotten_at 列；列不存在时
                # SELECT 报错 → 老库未 ALTER 成功 → 静默继续（不卡主流程）。
                log.debug(
                    "is_forgotten_recently failed (legacy DB?): %s", exc,
                )
            existing = await self._store.find_active(
                subject=fact.subject, key=fact.key
            )
            if existing is None:
                # Stage 2 cross-key 分支：先做矛盾扫描。
                if self._cross_key_merge_enabled:
                    try:
                        cross_persisted = await self._handle_cross_key(
                            fact, message_id,
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.warning(
                            "cross-key conflict scan failed: %s; "
                            "falling back to plain insert", exc,
                        )
                        cross_persisted = None
                    if cross_persisted is not None:
                        persisted.extend(cross_persisted)
                        continue
                fid = await self._store.upsert(
                    category=fact.category,
                    subject=fact.subject,
                    key=fact.key,
                    value=fact.value,
                    confidence=fact.confidence,
                    source_msg_id=message_id,
                    evidence=fact.evidence,
                    scope=fact.scope,
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
                    scope=fact.scope,
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
                    key=fact.key,
                )
                persisted.append({
                    "id": existing["id"], "action": "merge", **fact.__dict__,
                    "value": merged_value,
                })
            else:
                log.debug("FactExtractor: unknown merge action %r", decision.action)
        return persisted

    # ------------------------------------------------------------------
    # Stage 2 / WI-S2.1a — cross-key conflict scanning
    # ------------------------------------------------------------------

    async def _handle_cross_key(
        self, fact: "ExtractedFact", message_id: int,
    ) -> Optional[list[dict[str, Any]]]:
        """Cross-key 矛盾分支处理。

        Returns:
          * None — 跳过此分支（无候选 / flag 关 / 异常上抛），让调用方
            走 fallback insert 路径
          * list[persisted] — 已处理，含本 fact 的 insert / superseded 操作

        D3 v2 混合视野：① 最近 20 条 ② embedder 召回 10 条 (limited
        to same subject) ③ 合并去重，上限 25。
        """
        # ① 最近 20 条
        recent = await self._store.list_active(
            subject=fact.subject, limit=20,
        )
        # ② 语义最近 10 条
        semantic: list[dict[str, Any]] = []
        if self._embedder is not None:
            try:
                emb_arr = await self._embedder.encode([f"{fact.key}: {fact.value}"])
                if emb_arr is not None and len(emb_arr) > 0:
                    semantic = await self._store.vector_search_in_subject(
                        query_embedding=emb_arr[0],
                        subject=fact.subject,
                        limit=10,
                    )
            except Exception as exc:  # noqa: BLE001
                log.debug("cross_key semantic recall failed: %s", exc)
                semantic = []
        # ③ 合并去重
        candidates = _merge_dedupe_facts(recent, semantic, limit=25)
        if not candidates:
            return None

        decision = await self._decide_cross_key_conflict(
            new_fact=fact, candidates=candidates,
        )
        new_fid: Optional[int] = None
        persisted: list[dict[str, Any]] = []
        if decision.should_insert:
            new_fid = await self._store.upsert(
                category=fact.category,
                subject=fact.subject,
                key=fact.key,
                value=fact.value,
                confidence=fact.confidence,
                source_msg_id=message_id,
                evidence=fact.evidence,
                scope=fact.scope,
            )
            persisted.append(
                {"id": new_fid, "action": "insert", **fact.__dict__}
            )
        existing_ids = {int(r["id"]) for r in candidates if r.get("id") is not None}
        for entry in decision.conflicts:
            old_id_raw = entry.get("old_id") if isinstance(entry, dict) else None
            if not isinstance(old_id_raw, int):
                log.warning(
                    "cross_key: dropping conflict with non-int old_id: %r",
                    entry,
                )
                continue
            if old_id_raw not in existing_ids:
                log.warning(
                    "cross_key: LLM referenced unknown fact id=%s; ignoring",
                    old_id_raw,
                )
                continue
            if new_fid is None:
                # 没 insert 新 fact 时谁去标 superseded？保守 no-op + log。
                log.warning(
                    "cross_key: should_insert=False yet conflicts non-empty; "
                    "skipping mark_superseded for old_id=%s", old_id_raw,
                )
                continue
            await self._store.mark_superseded(
                old_id=old_id_raw, superseded_by=new_fid,
            )
            persisted.append({
                "id": old_id_raw,
                "action": "superseded",
                "superseded_by": new_fid,
            })
        if not decision.should_insert and not decision.conflicts:
            log.debug(
                "cross_key: LLM said should_insert=False with no conflicts; "
                "noop for new fact %s", fact,
            )
        return persisted

    async def _decide_cross_key_conflict(
        self, *, new_fact: "ExtractedFact",
        candidates: list[dict[str, Any]],
    ) -> "CrossKeyDecision":
        """LLM 看 new_fact + 候选 N 条 active facts，输出冲突判断。

        Prompt 不放 evidence（控长度，R1）；只放 id/key/value/updated_at。
        失败兜底（LLM error / JSON 解析失败）→ should_insert=True,
        conflicts=[]，等价于不做 cross-key 干预（TS1-3/4）。
        """
        lines = []
        now = time.time()
        for r in candidates:
            updated = float(r.get("updated_at") or 0.0)
            ago = max(0.0, (now - updated) / 86400.0)
            lines.append(
                f"  - id={r['id']} key={r['key']!r} "
                f"value={str(r.get('value',''))[:80]!r} "
                f"updated_ago_days={ago:.1f}"
            )
        # Stage 2 真测试 round 2 bug fix：LLM 缺 user 修正原话上下文
        # 会判"两个过敏原可以共存，不矛盾"。把 new fact 的 evidence
        # （normalize 截到 200 字以内）也传进 prompt，让 LLM 看到
        # "其实不是 X，是 Y" 类型的修正信号。
        prompt = _CROSS_KEY_CONFLICT_PROMPT.format(
            new_category=new_fact.category,
            new_subject=new_fact.subject,
            new_key=new_fact.key,
            new_value=new_fact.value,
            new_evidence=new_fact.evidence,
            candidates="\n".join(lines),
        )
        try:
            raw = await self._cross_key_llm(prompt)
        except Exception as exc:  # noqa: BLE001
            log.warning("cross_key LLM failed: %s", exc)
            return CrossKeyDecision(should_insert=True, conflicts=[])
        return _parse_cross_key_decision(raw)

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


_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_reasoning_blocks(text: str) -> str:
    """Stage 2 真测试 round 2 bug fix：DeepSeek-V4 / Claude reasoning /
    其他 thinking 模型常在 JSON 输出前附 ``<think>...</think>`` 块，
    且块内会出现 ``[``、``{``、``"`` 等字符让 ``find("[") / rfind("]")``
    截取范围出错或 ``json.loads`` 失败。先用正则把所有 think 块剥掉，
    再做 json 范围扫描更稳。

    其他常见 reasoning 标记（claude ``<thinking>``、gemini ``<reasoning>``）
    也覆盖。
    """
    text = _THINK_BLOCK_RE.sub("", text)
    for tag in ("thinking", "reasoning"):
        text = re.sub(
            rf"<{tag}>.*?</{tag}>", "", text, flags=re.DOTALL | re.IGNORECASE,
        )
    return text.strip()


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
    text = _strip_reasoning_blocks(text)
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


def _parse_cross_key_decision(raw: str) -> CrossKeyDecision:
    """Defensive parse for cross-key LLM output.

    Any non-conforming response → safe default ``should_insert=True,
    conflicts=[]`` —— 等价"什么都不动"，承接 Stage 1 行为。
    """
    if not raw:
        return CrossKeyDecision(should_insert=True, conflicts=[])
    text = raw.strip()
    if text.startswith("```"):
        nl = text.find("\n")
        if nl != -1:
            text = text[nl + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    text = _strip_reasoning_blocks(text)
    lb, rb = text.find("{"), text.rfind("}")
    if not (0 <= lb < rb):
        return CrossKeyDecision(should_insert=True, conflicts=[])
    try:
        obj = json.loads(text[lb:rb + 1])
    except json.JSONDecodeError:
        return CrossKeyDecision(should_insert=True, conflicts=[])
    if not isinstance(obj, dict):
        return CrossKeyDecision(should_insert=True, conflicts=[])
    raw_conflicts = obj.get("conflicts")
    out_conflicts: list[dict[str, Any]] = []
    if isinstance(raw_conflicts, list):
        for c in raw_conflicts:
            if not isinstance(c, dict):
                continue
            # 不在这里过滤 old_id 类型 —— 调用方会过滤 + 记 warning。
            out_conflicts.append(c)
    should_insert = bool(obj.get("should_insert", True))
    return CrossKeyDecision(
        should_insert=should_insert, conflicts=out_conflicts,
    )


def _merge_dedupe_facts(
    recent: list[dict[str, Any]],
    semantic: list[dict[str, Any]],
    *, limit: int = 25,
) -> list[dict[str, Any]]:
    """D3 v2：合并最近 N + 语义 M 候选，按 id 去重，限 limit。

    顺序：最近优先（更可能是用户刚说的）→ 语义补漏。
    """
    seen: dict[int, dict[str, Any]] = {}
    for src in (recent, semantic):
        for r in src:
            rid = r.get("id")
            if rid is None:
                continue
            seen.setdefault(int(rid), dict(r))
            if len(seen) >= limit:
                break
        if len(seen) >= limit:
            break
    return list(seen.values())[:limit]


def _parse_merge_decision(raw: str) -> MergeDecision:
    if not raw:
        return MergeDecision(action="no_op", value=None, reason="empty llm response")
    text = raw.strip()
    text = _strip_reasoning_blocks(text)
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
