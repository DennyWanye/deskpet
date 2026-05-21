"""Phase C — LLM-driven query rewriter for short / ambiguous queries.

Why
---
Short conversational queries ("那个事情怎么样了?") carry insufficient
signal for vector / FTS recall. A small LLM can expand them using
recent chat context into a denser, more retrievable form.

This is **only triggered for queries below the length threshold** (see
``Retriever._maybe_rewrite_query``). Long queries are left alone — they
already carry enough information.

The rewriter is duck-typed: callers expect ``await rewrite(query)``.
Two concrete implementations:

* :class:`LLMQueryRewriter` — production. Wraps any async LLM callable.
* :class:`NoopQueryRewriter` — for tests and dev — returns input as-is.

Failure-isolation: the consumer (``Retriever._maybe_rewrite_query``)
catches exceptions and falls back to the original query, so a flaky
LLM never breaks recall.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

log = logging.getLogger(__name__)


_LLMCall = Callable[[str], Awaitable[str]]


_REWRITE_PROMPT = """\
You are expanding a short user query for better semantic retrieval.

USER QUERY: {query}

Recent context (most recent first; may be empty):
{context}

Rewrite the query into a more self-contained, retrieval-friendly form.
Preserve the user's intent. Keep it in the SAME LANGUAGE as the query.
Output ONLY the rewritten query as one line. No quotes, no prefix.

REWRITTEN:"""


class NoopQueryRewriter:
    """Returns input unchanged. Used in tests + when feature flag is off."""

    async def rewrite(self, query: str, *, context: str = "") -> str:
        return query


class LLMQueryRewriter:
    """Async wrapper that asks an LLM to expand short queries."""

    def __init__(
        self,
        llm_call: _LLMCall,
        *,
        timeout_s: float = 5.0,
    ) -> None:
        self._llm = llm_call
        self._timeout = timeout_s

    async def rewrite(self, query: str, *, context: str = "") -> str:
        if not query or not query.strip():
            return query
        prompt = _REWRITE_PROMPT.format(
            query=query.strip(),
            context=context.strip()[:1000] or "(no context)",
        )
        try:
            raw = await asyncio.wait_for(self._llm(prompt), timeout=self._timeout)
        except asyncio.TimeoutError:
            log.debug("LLMQueryRewriter: timed out, returning original")
            return query
        except Exception as exc:  # noqa: BLE001
            log.debug("LLMQueryRewriter: LLM failed: %s", exc)
            return query
        rewritten = (raw or "").strip()
        # Strip wrapping quotes if the LLM ignored the "no quotes" instruction.
        if len(rewritten) >= 2 and rewritten[0] in '"\'' and rewritten[-1] in '"\'':
            rewritten = rewritten[1:-1].strip()
        # Reject empty / whitespace-only rewrites — fall back to original.
        if not rewritten:
            return query
        # Reject obvious failure markers (LLM apologised, refused, etc.)
        lower = rewritten.lower()
        if any(bad in lower for bad in ("i cannot", "i can't", "抱歉", "无法")):
            return query
        return rewritten
