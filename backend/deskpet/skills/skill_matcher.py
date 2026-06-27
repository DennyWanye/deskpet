# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""SkillMatcher — embedding-based skill similarity matching (WI-4.1).

Computes cosine similarity between an incoming query and each skill's
description (+when_to_use if present) to rank which skills are most
relevant to the current user turn.

Design notes
------------
* ``embedder`` is injected so no second model instance is created —
  the existing BGE-M3 (or any embedder with ``encode(text) -> list[float]``)
  is reused from the retrieval stack.
* Skill embeddings are pre-computed in ``build(skills)`` and cached in a
  dict to avoid per-query recomputation. Cache is invalidated on each
  ``build()`` call (called from ``SkillLoader.reload()``).
* Query embedding is computed freshly per ``match()`` call (query is
  per-turn; not cacheable).
* If ``embedder`` is None (offline / not yet initialised), ``match()``
  returns ``[]`` and the caller degrades gracefully to desc-list-only.
* ``encode`` may be a sync CPU-bound call; ``match_async`` wraps it in
  ``asyncio.to_thread`` to avoid blocking the event loop (T2 requirement).
"""
from __future__ import annotations

import asyncio
import inspect
import math
from typing import Any, Optional


# Similarity assigned to an explicit trigger-phrase hit — must clear any
# reasonable strong_threshold so trigger matches always auto-disclose.
_TRIGGER_SIM = 0.95


class SkillMatcher:
    """Ranks skills by cosine similarity to a query.

    Parameters
    ----------
    embedder:
        Any object with ``encode(text: str) -> list[float]`` (sync is fine;
        ``match_async`` wraps in ``asyncio.to_thread``). Pass ``None`` to
        run in degraded mode (always returns empty list).
    """

    def __init__(self, embedder: Optional[Any]) -> None:
        self._embedder = embedder
        # name → pre-computed embedding vector
        self._cache: dict[str, list[float]] = {}

    # ------------------------------------------------------------------
    # Build / invalidate
    # ------------------------------------------------------------------

    def build(self, skills: list[Any]) -> None:
        """Pre-compute embeddings for all skill descriptions.

        Called once on loader start and after each reload. ``skills`` is a
        list of duck-typed objects with at least ``.name``, ``.description``
        and optionally ``.when_to_use`` attributes (or keys if dict).

        If the embedder is unavailable the cache is cleared so we don't
        serve stale embeddings from a prior build.
        """
        self._cache = {}
        if self._embedder is None:
            return
        # 生产 BGE-M3 的 encode 是 async coroutine function — sync build 调它
        # 只会拿到未 await 的 coroutine（TC-5.1：原实现 _normalise(coroutine)
        # 抛异常被吞 → build 静默 no-op + unawaited warning）。直接跳过，
        # 交给 build_async 预热或 match_async 惰性建缓存。
        encode_fn = getattr(self._embedder, "encode", None)
        if inspect.iscoroutinefunction(encode_fn) or inspect.iscoroutinefunction(
            getattr(self._embedder, "embed", None)
        ):
            return
        for skill in skills:
            name = _skill_attr(skill, "name", "")
            description = _skill_attr(skill, "description", "") or ""
            when_to_use = _skill_attr(skill, "when_to_use", "") or ""
            text = description
            if when_to_use:
                text = f"{description}\n{when_to_use}"
            if not text.strip() or not name:
                continue
            try:
                vec = self._embedder.encode(text)
                if inspect.isawaitable(vec):
                    vec.close()  # defensive: async-returning sync attr
                    continue
                self._cache[name] = _normalise(vec)
            except Exception:  # noqa: BLE001 — degrade silently
                pass

    async def build_async(self, skills: list[Any]) -> int:
        """Async pre-warm of the skill embedding cache (lifespan entry).

        Works with BOTH embedder contracts via ``_embed_one_async``. Returns
        the number of skills cached. Existing cache entries are kept (idempotent
        with the lazy rebuild in ``match_async``).
        """
        if self._embedder is None:
            return 0
        cached = 0
        for skill in skills:
            name = _skill_attr(skill, "name", "")
            if not name or name in self._cache:
                continue
            description = _skill_attr(skill, "description", "") or ""
            when_to_use = _skill_attr(skill, "when_to_use", "") or ""
            text = f"{description}\n{when_to_use}" if when_to_use else description
            if not text.strip():
                continue
            vec = await self._embed_one_async(text)
            if vec:
                self._cache[name] = _normalise(vec)
                cached += 1
        return cached

    # ------------------------------------------------------------------
    # Synchronous match
    # ------------------------------------------------------------------

    def match(self, query: str, skills: list[Any]) -> list[tuple[str, float]]:
        """Compute cosine similarity between ``query`` and each skill.

        Returns a list of ``(name, similarity)`` sorted descending by
        similarity. Skills not in the cache (no embedding) are omitted.

        If the embedder is None or the cache is empty, returns ``[]``.
        """
        if self._embedder is None or not self._cache:
            return []
        if not skills:
            return []
        try:
            query_vec = _normalise(self._embedder.encode(query))
        except Exception:  # noqa: BLE001
            return []

        results: list[tuple[str, float]] = []
        for skill in skills:
            name = _skill_attr(skill, "name", "")
            skill_vec = self._cache.get(name)
            if skill_vec is None:
                continue
            sim = _cosine_sim(query_vec, skill_vec)
            results.append((name, sim))

        results.sort(key=lambda x: x[1], reverse=True)
        return results

    # ------------------------------------------------------------------
    # Async variant (avoids blocking event loop for sync CPU-bound encode)
    # ------------------------------------------------------------------

    async def _embed_one_async(self, text: str) -> list[float]:
        """Embed a single text, tolerating BOTH embedder contracts:

        * 生产 BGE-M3：``async embed(list[str]) -> list[list[float]]`` /
          ``async encode(list[str]) -> ndarray``（异步、收 list、自动 warmup）。
        * 单测 mock：``encode(text:str) -> list[float]``（同步）。

        FP-5 缺口 5h (2026-06-06 真机抓 bug)：原 ``build()``/``match()`` 同步调
        ``encode(text)``，但生产 embedder 的 ``encode`` 是 **async + 收 list**，
        同步调拿到的是未 await 的 coroutine → ``_normalise`` 迭代它抛异常 → 被
        ``except: pass`` 吞 → 缓存恒空 → 自动披露永远零匹配（top_sim=0.0）。
        单测用 sync mock 所以全绿，掩盖了这条生产死链。
        """
        emb = self._embedder
        if emb is None:
            return []
        embed_fn = getattr(emb, "embed", None)
        try:
            if callable(embed_fn):
                # Production async contract: embed(list[str]) -> list[list[float]].
                res = embed_fn([text])
                if inspect.isawaitable(res):
                    res = await res
                if res is not None and len(res) > 0:
                    return [float(x) for x in res[0]]
                return []
            # Fallback: encode. Try list-form first (async list->ndarray contract).
            res = emb.encode([text])
            if inspect.isawaitable(res):
                res = await res
            if res is None:
                return []
            # res is list-of-vectors (we passed [text]) → take row 0.
            try:
                return [float(x) for x in res[0]]
            except (TypeError, IndexError, KeyError):
                # Some sync embedders return a single vector regardless of input.
                return [float(x) for x in res]
        except Exception:  # noqa: BLE001 — degrade silently
            return []

    async def match_async(self, query: str, skills: list[Any]) -> list[tuple[str, float]]:
        """Async cosine match using the (possibly async) embedder.

        Builds/repairs the per-skill embedding cache lazily here — this is
        the robust path that works regardless of when ``build()`` ran
        relative to embedder warmup (FP-5 缺口 5h/5i：原 ``build()`` 在
        BGE-M3 subprocess ready 之前跑 + 同步调 async encode → 缓存空/零).
        """
        if not skills:
            return []

        # Lazy async (re)build: embed any selected skill missing from cache.
        # (embedder=None → 跳过 embedding,trigger 词法路仍然工作)
        if self._embedder is not None:
            for skill in skills:
                name = _skill_attr(skill, "name", "")
                if not name or name in self._cache:
                    continue
                description = _skill_attr(skill, "description", "") or ""
                when_to_use = _skill_attr(skill, "when_to_use", "") or ""
                text = f"{description}\n{when_to_use}" if when_to_use else description
                if not text.strip():
                    continue
                vec = await self._embed_one_async(text)
                if vec:
                    self._cache[name] = _normalise(vec)

        query_vec = _normalise(await self._embed_one_async(query))
        have_query_vec = any(query_vec)

        # TC-5.1 (2026-06-11)：混合匹配。BGE-M3 对短中文 query 的余弦区分度
        # 不够(on-target 0.45~0.55 vs off-target 0.53+,实测 8 query 校准)——
        # 显式 triggers 命中(query 含触发词)直接抬到 _TRIGGER_SIM(>任何
        # strong_threshold)，embedding 兜没写 trigger 的 paraphrase。
        results: list[tuple[str, float]] = []
        query_lower = query.lower()
        for skill in skills:
            name = _skill_attr(skill, "name", "")
            if not name:
                continue
            skill_vec = self._cache.get(name)
            triggers = _skill_attr(skill, "triggers", None) or []
            hit_trigger = any(
                t and str(t).lower() in query_lower for t in triggers
            )
            # 保持原契约：没向量也没 trigger 命中的 skill 不进结果。
            if skill_vec is None and not hit_trigger:
                continue
            sim = 0.0
            if have_query_vec and skill_vec is not None:
                sim = _cosine_sim(query_vec, skill_vec)
            if hit_trigger:
                sim = max(sim, _TRIGGER_SIM)
            results.append((name, sim))
        results.sort(key=lambda x: x[1], reverse=True)
        return results


# ---------------------------------------------------------------------------
# Vector utilities
# ---------------------------------------------------------------------------

def _normalise(vec: list[float]) -> list[float]:
    """Return L2-normalised vector. Zero vector → zero vector (no division)."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm < 1e-12:
        return list(vec)
    return [x / norm for x in vec]


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two L2-normalised vectors."""
    n = min(len(a), len(b))
    return sum(a[i] * b[i] for i in range(n))


def _skill_attr(s: Any, attr: str, default: Any = None) -> Any:
    if isinstance(s, dict):
        return s.get(attr, default)
    return getattr(s, attr, default)


__all__ = ["SkillMatcher"]
