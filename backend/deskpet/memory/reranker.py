# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Phase C — Cross-encoder reranker for retriever output.

Two implementations, identical interface:

* :class:`BGEReranker` — wraps ``BAAI/bge-reranker-v2-m3`` (FlagEmbedding's
  ``FlagReranker``). 127 MB CPU model; same mock-fallback story as
  :mod:`deskpet.memory.embedder`.
* :class:`MockReranker` — deterministic hash-based pseudo-scores. Used
  for tests, for fresh installs that don't yet have the weights, and for
  unit-test environments without FlagEmbedding installed.

Both expose::

    async def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        *,
        top_k: int | None = None,
    ) -> list[tuple[int, float]]

The contract: candidates is ``[{"message_id": int, "text": str}, ...]``.
Return ``[(message_id, score)]`` sorted by score descending. Lengths can
differ from input (e.g. reranker may drop items with empty text).

Failure-isolation: the caller (Retriever._apply_reranker) wraps the
``await reranker.rerank(...)`` call in try/except and falls back to the
RRF order on any exception, so reranker breakage never breaks recall.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)


class MockReranker:
    """Deterministic hash-based reranker for tests / fresh installs.

    Score is the negative absolute byte-level XOR of md5(query) and
    md5(text), so identical query + text pairs always rank identically.
    Not a real model — for unit-test plumbing only.
    """

    def __init__(self) -> None:
        self._is_mock = True

    def is_mock(self) -> bool:
        return True

    async def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        *,
        top_k: int | None = None,
    ) -> list[tuple[int, float]]:
        if not candidates:
            return []
        q_digest = hashlib.md5(query.encode("utf-8")).digest()
        scored: list[tuple[int, float]] = []
        for c in candidates:
            mid = int(c.get("message_id", 0))
            text = str(c.get("text") or "")
            if not text:
                continue
            d = hashlib.md5(text.encode("utf-8")).digest()
            # Score: number of matching bytes (0..16). Higher = "more
            # similar" in mock-land.
            score = sum(1 for a, b in zip(q_digest, d) if a == b)
            # Bonus for exact substring match — gives the test a way to
            # actually verify "good" reorderings deterministically.
            if query.strip() and query.strip() in text:
                score += 100
            scored.append((mid, float(score)))
        scored.sort(key=lambda t: (-t[1], t[0]))
        if top_k is not None:
            scored = scored[:top_k]
        return scored


class BGEReranker:
    """``BAAI/bge-reranker-v2-m3`` wrapped behind the same async API.

    Loads lazily on first ``rerank()`` call so cold-start cost only hits
    sessions that actually use rerank. If the model directory is absent
    or :mod:`FlagEmbedding` import fails, we transparently fall back to
    :class:`MockReranker` semantics — exactly the same shape, just
    scored by hash.
    """

    def __init__(
        self,
        model_path: Optional[Path] = None,
        *,
        device: str = "cpu",
        use_mock_when_missing: bool = True,
    ) -> None:
        self._model_path = Path(model_path) if model_path else None
        self._device = device
        self._use_mock = use_mock_when_missing
        self._model: Any = None
        self._mock = MockReranker()
        self._mock_mode = False
        self._lock = asyncio.Lock()

    def is_mock(self) -> bool:
        return self._mock_mode or self._model is None and self._model_path is None

    async def _ensure_loaded(self) -> None:
        if self._model is not None or self._mock_mode:
            return
        async with self._lock:
            if self._model is not None or self._mock_mode:
                return
            if self._model_path is None or not self._model_path.exists():
                if not self._use_mock:
                    raise RuntimeError(
                        f"reranker model not found at {self._model_path}"
                    )
                log.info("BGEReranker: weights absent → using MockReranker")
                self._mock_mode = True
                return
            try:
                # Lazy import — FlagEmbedding is optional dep.
                from FlagEmbedding import FlagReranker  # type: ignore
            except ImportError:
                if not self._use_mock:
                    raise
                log.info("BGEReranker: FlagEmbedding not installed → MockReranker")
                self._mock_mode = True
                return
            loop = asyncio.get_running_loop()
            self._model = await loop.run_in_executor(
                None,
                lambda: FlagReranker(
                    str(self._model_path), use_fp16=True
                ),
            )

    async def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        *,
        top_k: int | None = None,
    ) -> list[tuple[int, float]]:
        await self._ensure_loaded()
        if self._mock_mode:
            return await self._mock.rerank(query, candidates, top_k=top_k)
        if not candidates:
            return []
        pairs = [(query, str(c.get("text") or "")) for c in candidates]
        loop = asyncio.get_running_loop()
        scores = await loop.run_in_executor(
            None,
            lambda: self._model.compute_score(pairs, normalize=True),
        )
        # FlagReranker returns either a scalar (single pair) or list.
        if isinstance(scores, (int, float)):
            scores = [float(scores)]
        scored: list[tuple[int, float]] = []
        for c, sc in zip(candidates, scores):
            mid = int(c.get("message_id", 0))
            scored.append((mid, float(sc)))
        scored.sort(key=lambda t: (-t[1], t[0]))
        if top_k is not None:
            scored = scored[:top_k]
        return scored
