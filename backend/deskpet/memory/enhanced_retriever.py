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
    ) -> None:
        self._base = base
        self._facts_store = facts_store
        self._facts_weight = float(facts_weight)
        self._reranker = reranker
        self._query_rewriter = query_rewriter

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
            self._facts_store is None or self._facts_weight <= 0
        ) and self._reranker is None:
            return base_hits[:effective_top_k]

        merged = list(base_hits)
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
        try:
            rows = await self._facts_store.search(query, limit=top_k)
        except Exception as exc:  # noqa: BLE001
            log.debug("facts search failed: %s", exc)
            return []
        out: list[Hit] = []
        for r in rows:
            fid = int(r["id"])
            synth = _FACT_ID_OFFSET + fid
            text = f"[fact] {r['key']}: {r['value']}"
            if r.get("evidence"):
                text += f"  (来源: {r['evidence']})"
            ts = float(r.get("updated_at") or 0.0)
            out.append(Hit(
                message_id=synth,
                score=float(r.get("confidence") or 0.5),
                text=text,
                ts=ts,
                source="facts",
            ))
        return out

    async def _apply_reranker(
        self, query: str, hits: list[Hit]
    ) -> list[Hit]:
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
