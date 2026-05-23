"""Phase B+C — EnhancedRetriever: non-invasive wrapper over Retriever.

Rationale
---------
The base ``deskpet.memory.retriever.Retriever`` is stable, well-tested,
and integrated with session-affinity (OpenSpec D1). Modifying it
in-place to add Phase B (facts) + Phase C (rerank / chunk / rewrite)
risks regressing the existing 1200+ tests and burns trust.

Instead, this module implements a **wrapper** that:

  1. Holds a reference to the legacy ``Retriever`` instance.
  2. Optionally holds a :class:`FactsStore` / reranker / query rewriter.
  3. Exposes the same ``recall(query, top_k, ...)`` API surface.
  4. Post-processes the legacy ``recall()`` output to:
     * fold in fact rows from FactsStore (as synthetic Hit entries with
       message_id offset by ``_FACT_ID_OFFSET``);
     * apply cross-encoder rerank on the merged top-N;
     * optionally rewrite short queries before delegation.

Strangler-Fig: any of the plug-ins can be ``None`` and the wrapper
falls back to a pass-through. With ALL plug-ins None the wrapper's
result is byte-identical to ``Retriever.recall()`` — same ``Hit``
objects, same order, same scores.

Public surface mirrors :class:`Retriever` so callers can swap one for
the other without import changes.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from typing import Any, Optional

import aiosqlite

from deskpet.memory.retriever import Hit, Retriever

log = logging.getLogger(__name__)


# Synthetic message_id offset for fact rows — same value used by
# MemoryComponent renderers and Phase A eval (kept here too so callers
# can import from a stable location).
_FACT_ID_OFFSET = 1_000_000_000_000_000

# How many top RRF-fused candidates feed the cross-encoder reranker.
_RERANK_INPUT_K = 30

# Short-query threshold for triggering query rewriting.
_SHORT_QUERY_CHARS = 20


class EnhancedRetriever:
    """Wraps a :class:`Retriever` with optional Phase B/C plug-ins.

    Parameters
    ----------
    base:
        The legacy retriever. Never modified by this wrapper.
    facts_store:
        Optional :class:`FactsStore`. When provided AND ``facts_weight``
        > 0 the wrapper folds fact rows into the recall result.
    facts_weight:
        Multiplier applied to fact RRF contributions (default 0.0 means
        "do not fold facts at all"). Range 0..1 typical; values > 1
        amplify fact dominance.
    reranker:
        Optional duck-typed reranker (see ``deskpet.memory.reranker``).
        Must expose ``async rerank(query, candidates) -> [(mid, score)]``.
    query_rewriter:
        Optional duck-typed rewriter (see ``deskpet.memory.query_rewriter``).
        Must expose ``async rewrite(query) -> str``.
    """

    def __init__(
        self,
        base: Retriever,
        *,
        facts_store: Any | None = None,
        facts_weight: float = 0.0,
        reranker: Any | None = None,
        query_rewriter: Any | None = None,
        embedder: Any | None = None,
        chunk_store: Any | None = None,
    ) -> None:
        self._base = base
        self._facts_store = facts_store
        self._facts_weight = float(facts_weight)
        self._reranker = reranker
        self._query_rewriter = query_rewriter
        # 记忆系统升级 WI-M1.5：chunk 召回。命中 chunk → 折叠 parent
        # message。只「补」base 漏掉的消息（不重排 base），保证不回归。
        self._chunk_store = chunk_store
        # 记忆系统升级 WI-M1.4 / PRD §3.1：facts 走向量召回需要 query
        # embedder。None / mock 时 _collect_fact_hits 降级到 LIKE 子串兜底。
        self._embedder = embedder
        # 记忆系统升级 WI-M1.3 / 评审缺口 6：mock reranker（hash 打分）会
        # 主动打乱召回顺序 → eval 回归。检测到 mock 时自动 bypass 重排，
        # 只 warn 一次。
        self._rerank_mock_warned = False

    @property
    def policy(self):
        """Surface the base policy for callers (Retrieval policy peek)."""
        return self._base.policy

    @property
    def base(self) -> Retriever:
        return self._base

    @property
    def facts_store(self) -> Any:
        return self._facts_store

    # ------------------------------------------------------------------
    # Public entrypoint
    # ------------------------------------------------------------------

    async def recall(
        self,
        query: str,
        top_k: int | None = None,
        *,
        cur_session_id: str | None = None,
        cur_session_kind: str | None = None,
        cross_session_decay: float | None = None,
    ) -> list[Hit]:
        """Wrapped recall.

        Pipeline:
            1. Maybe rewrite a short query (Phase C).
            2. Delegate to base ``Retriever.recall`` with the effective
               query, asking for a **wider** top_k window so we have
               headroom after merging facts + reranking.
            3. Maybe add fact hits (Phase B) — synthetic IDs.
            4. Maybe rerank top-N (Phase C).
            5. Slice to caller's ``top_k`` and return.

        Each post-processing step is independently optional: with all
        plug-ins ``None`` the output is byte-identical to ``base.recall``.
        """
        effective_query = await self._maybe_rewrite(query)
        effective_top_k = top_k if top_k is not None else self._base.policy.top_k

        # Ask base for a wider window so we don't truncate good candidates
        # before merge + rerank get a chance.
        widened_k = max(effective_top_k, _RERANK_INPUT_K) if self._reranker else effective_top_k
        base_hits = await self._base.recall(
            effective_query,
            top_k=widened_k,
            cur_session_id=cur_session_id,
            cur_session_kind=cur_session_kind,
            cross_session_decay=cross_session_decay,
        )

        # No plug-ins active → byte-identical legacy path.
        if (
            (self._facts_store is None or self._facts_weight <= 0)
            and self._reranker is None
            and self._chunk_store is None
        ):
            return base_hits[:effective_top_k]

        merged = list(base_hits)
        # WI-M1.5：chunk 召回 —— 只补 base 漏掉的 message（追加到尾部，
        # 不动 base 顺序）。base 已有的 message 不重复加。
        if self._chunk_store is not None:
            chunk_hits = await self._collect_chunk_hits(
                effective_query, top_k=widened_k
            )
            merged = _merge_chunk_hits(merged, chunk_hits)
        if self._facts_store is not None and self._facts_weight > 0:
            fact_hits = await self._collect_fact_hits(
                effective_query, top_k=widened_k
            )
            merged = _merge_with_facts(merged, fact_hits, self._facts_weight)

        if self._reranker is not None and merged:
            merged = await self._apply_reranker(effective_query, merged)

        return merged[:effective_top_k]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _maybe_rewrite(self, query: str) -> str:
        if not query or not query.strip():
            return query
        if self._query_rewriter is None:
            return query
        if len(query.strip()) >= _SHORT_QUERY_CHARS:
            return query
        try:
            out = await self._query_rewriter.rewrite(query)
        except Exception as exc:  # noqa: BLE001
            log.debug("query rewriter failed for %r: %s", query, exc)
            return query
        if not out or not str(out).strip():
            return query
        return str(out)

    async def _collect_fact_hits(
        self, query: str, *, top_k: int
    ) -> list[Hit]:
        """记忆系统升级 WI-M1.4 / PRD §3.1：facts 走**向量召回**。

        中文整句 LIKE 子串匹配几乎不命中 —— 改为对 query embedding 与
        facts 表的 ``embedding`` 列做 brute-force cosine。降级链：
          * 无 embedder / embedder mock → 直接 LIKE 兜底（mock 时 facts
            行也没写 embedding，向量召回必空）。
          * 有真 embedder 但向量召回空（如老 fact 无 embedding）→ 再
            LIKE 兜底一次。
        """
        rows: list[Any] = []
        emb = self._embedder
        emb_usable = emb is not None and not (
            hasattr(emb, "is_mock") and emb.is_mock()
        )
        if emb_usable:
            try:
                qvec = await emb.encode([query])
                if qvec is not None and len(qvec) > 0:
                    rows = await self._facts_store.vector_search(
                        qvec[0], limit=top_k
                    )
            except Exception as exc:  # noqa: BLE001
                log.debug("facts vector_search failed: %s", exc)
                rows = []
        if not rows:
            # LIKE 兜底（embedder 缺失 / mock / 向量召回空）。
            try:
                rows = await self._facts_store.search(query, limit=top_k)
            except Exception as exc:  # noqa: BLE001
                log.debug("facts LIKE search failed: %s", exc)
                return []
        out: list[Hit] = []
        for r in rows:
            fid = int(r["id"])
            synth = _FACT_ID_OFFSET + fid
            text = f"[fact] {r['key']}: {r['value']}"
            if r.get("evidence"):
                text += f"  (来源: {r['evidence']})"
            ts = float(r.get("updated_at") or 0.0)
            # 向量召回行带 cosine ``_score``（0..1）；LIKE 兜底行用
            # confidence 当分。两者同量级，_merge_with_facts 据此折叠。
            score = r.get("_score")
            if score is None:
                score = r.get("confidence") or 0.5
            out.append(Hit(
                message_id=synth,
                score=float(score),
                text=text,
                ts=ts,
                source="facts",
            ))
        return out

    async def _collect_chunk_hits(
        self, query: str, *, top_k: int
    ) -> list[Hit]:
        """WI-M1.5：对 messages_chunks 做向量召回，命中 chunk → parent Hit。

        embedder 缺失 / mock 时 chunk 无向量 → vector_search 空 → 返回 []
        （chunk 路对 mock 环境是 no-op）。
        """
        emb = self._embedder
        if emb is None or (hasattr(emb, "is_mock") and emb.is_mock()):
            return []
        try:
            qvec = await emb.encode([query])
            if qvec is None or len(qvec) == 0:
                return []
            rows = await self._chunk_store.vector_search(qvec[0], limit=top_k)
        except Exception as exc:  # noqa: BLE001
            log.debug("chunk vector_search failed: %s", exc)
            return []
        out: list[Hit] = []
        for r in rows:
            out.append(Hit(
                message_id=int(r["message_id"]),
                score=float(r.get("_score") or 0.0),
                text=str(r.get("text") or ""),
                ts=0.0,
                source="chunk",
            ))
        return out

    async def _apply_reranker(
        self, query: str, hits: list[Hit]
    ) -> list[Hit]:
        # WI-M1.3 / 评审缺口 6：mock reranker bypass。BGEReranker 须先
        # 触发 lazy load 才能确定 mock 与否（_mock_mode 在 _ensure_loaded
        # 里才置位）；MockReranker 的 is_mock() 恒 True。
        rr = self._reranker
        _ensure = getattr(rr, "_ensure_loaded", None)
        if _ensure is not None:
            try:
                await _ensure()
            except Exception as exc:  # noqa: BLE001
                log.debug("reranker ensure_loaded failed: %s", exc)
        if getattr(rr, "is_mock", lambda: False)():
            if not self._rerank_mock_warned:
                log.warning(
                    "reranker 处于 mock 模式（hash 打分会打乱召回顺序）—— "
                    "自动 bypass 重排，召回顺序保持 RRF 原序。"
                )
                self._rerank_mock_warned = True
            return hits
        head = hits[:_RERANK_INPUT_K]
        if not head:
            return hits
        candidates = [
            {"message_id": h.message_id, "text": h.text}
            for h in head
            if h.text
        ]
        if not candidates:
            return hits
        try:
            scored = await self._reranker.rerank(query, candidates)
        except Exception as exc:  # noqa: BLE001
            log.debug("reranker failed: %s", exc)
            return hits
        if not scored:
            return hits
        by_id = {h.message_id: h for h in head}
        reordered: list[Hit] = []
        seen: set[int] = set()
        for mid, score in scored:
            mid = int(mid)
            base = by_id.get(mid)
            if base is None:
                continue
            reordered.append(replace(base, score=float(score)))
            seen.add(mid)
        # Any head hits the reranker dropped (e.g. empty text) stay in
        # original order at the tail.
        tail_head = [h for h in head if h.message_id not in seen]
        # And any items beyond _RERANK_INPUT_K stay at the very end.
        rest = hits[_RERANK_INPUT_K:]
        return reordered + tail_head + rest


def build_recall_retriever(
    base: Retriever,
    *,
    rerank: bool,
    enhanced_retriever: bool,
    query_rewrite: bool,
    chunking: bool,
    facts_store: Any | None = None,
    facts_weight: float = 0.2,
    reranker: Any | None = None,
    query_rewriter: Any | None = None,
    embedder: Any | None = None,
    chunk_store: Any | None = None,
) -> Any:
    """记忆系统升级 WI-M1.3/M1.4/M1.5：根据 v2 flag 决定召回器。

    所有 flag 全关 → 直接返回**裸 base Retriever**（recall 与第一代逐字节
    一致，Strangler-Fig）。任一开 → 用 :class:`EnhancedRetriever` 包住。

    把「flag → 包不包」的判定抽成纯函数，让 main.py 接线与单测共用同一
    逻辑（TG-3/4：断言 MemoryManager 持有的召回器类型随 flag 切换）。
    """
    if not (rerank or enhanced_retriever or query_rewrite or chunking):
        return base
    return EnhancedRetriever(
        base=base,
        facts_store=facts_store if enhanced_retriever else None,
        # facts_weight 必须显式传（D5）：默认 0.0 = facts 永不进结果。
        facts_weight=facts_weight if enhanced_retriever else 0.0,
        reranker=reranker if rerank else None,
        query_rewriter=query_rewriter if query_rewrite else None,
        embedder=embedder,
        chunk_store=chunk_store if chunking else None,
    )


def _merge_chunk_hits(
    base_hits: list[Hit],
    chunk_hits: list[Hit],
) -> list[Hit]:
    """把 chunk 召回命中的 parent message 补进结果。

    策略（保守、保证不回归）：base 已有的 message_id 一律跳过 —— chunk
    召回**只补漏**，不重排 base。新 message 按 chunk cosine 分降序追加到
    base 尾部。后续若有 reranker，由 reranker 统一重排。
    """
    if not chunk_hits:
        return base_hits
    seen = {h.message_id for h in base_hits}
    extra = [h for h in chunk_hits if h.message_id not in seen]
    extra.sort(key=lambda h: -h.score)
    return list(base_hits) + extra


def _merge_with_facts(
    base_hits: list[Hit],
    fact_hits: list[Hit],
    facts_weight: float,
) -> list[Hit]:
    """Merge fact hits into base hits.

    Strategy: scale fact scores by ``facts_weight`` and union with base
    hits. Sort by score descending. Stable tie-break by original index.
    """
    if not fact_hits:
        return base_hits
    merged: list[tuple[float, int, Hit]] = []
    for i, h in enumerate(base_hits):
        merged.append((h.score, i, h))
    base_count = len(base_hits)
    for j, h in enumerate(fact_hits):
        scaled = replace(h, score=h.score * facts_weight)
        merged.append((scaled.score, base_count + j, scaled))
    merged.sort(key=lambda t: (-t[0], t[1]))
    return [h for _, _, h in merged]
