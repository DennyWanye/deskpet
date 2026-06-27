# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Phase C — Sentence-level chunker for long messages.

Why
---
``Retriever`` currently embeds **whole messages** as one vector. A 2000-
character monologue gets compressed to one 1024-d point — information
gets lost in the averaging. Solution: split long messages into sentence
chunks, embed each chunk independently, store in ``messages_chunks``,
and at recall time return the parent ``message_id`` + the best-matching
chunk's text for highlighting.

This module deliberately implements ONLY the splitting / persistence
piece. The recall-time wiring (chunks → messages_vec → RRF) is bolted
into :mod:`deskpet.memory.retriever` via the same ``_facts_recall``-
style plumbing.

Strangler-fig: when no message exceeds ``_MIN_CHUNK_THRESHOLD`` chars
or when ``MessageChunker`` is never invoked, the ``messages_chunks``
table stays empty and the legacy whole-message embedding path is
unaffected.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from deskpet.memory.memory_v2_schema import ensure_memory_v2_tables

log = logging.getLogger(__name__)


# Don't bother chunking anything below this character count — the
# whole-message embedding is already representative. Tuned by hand for
# CJK + English mixed chat (~500 chars ≈ 2-3 long sentences).
_MIN_CHUNK_THRESHOLD = 500

# Each chunk targets approximately this many characters. Sentence
# boundaries are honoured; we don't split mid-sentence.
_TARGET_CHUNK_CHARS = 280

# Hard cap — even one mega-sentence over this gets force-split on word
# / character boundaries to keep BGE-M3 input ≤ its 512 token limit.
_MAX_CHUNK_CHARS = 480


# Sentence-boundary regex. Handles CJK punctuation (。！？), English
# .?!, and explicit newline groups. Capture group preserves the punct
# in the chunk for natural readability.
_SENTENCE_SPLIT_RE = re.compile(
    r"(?<=[。！？\.\!\?])\s+|\n{2,}",
    flags=re.MULTILINE,
)


def split_into_chunks(text: str) -> list[str]:
    """Pure function: split text into sentence-level chunks.

    Rules:
      * Empty / whitespace → returns [].
      * Length < ``_MIN_CHUNK_THRESHOLD`` → returns [text.strip()]
        (one chunk = whole message).
      * Otherwise, sentence-split, greedily pack sentences until target
        chunk size reached, then start a new chunk.
      * Sentences exceeding ``_MAX_CHUNK_CHARS`` get hard-split on
        character boundary (rare; happens for code dumps).
    """
    if not text or not text.strip():
        return []
    text = text.strip()
    if len(text) < _MIN_CHUNK_THRESHOLD:
        return [text]

    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    if not sentences:
        return [text]

    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for sent in sentences:
        if len(sent) > _MAX_CHUNK_CHARS:
            # Flush current buffer
            if buf:
                chunks.append(" ".join(buf))
                buf, buf_len = [], 0
            # Hard-split the over-long sentence
            for i in range(0, len(sent), _MAX_CHUNK_CHARS):
                chunks.append(sent[i:i + _MAX_CHUNK_CHARS])
            continue
        if buf_len + len(sent) > _TARGET_CHUNK_CHARS and buf:
            chunks.append(" ".join(buf))
            buf, buf_len = [sent], len(sent)
        else:
            buf.append(sent)
            buf_len += len(sent) + 1  # +1 for the space join
    if buf:
        chunks.append(" ".join(buf))
    return chunks


class MessageChunker:
    """Persist chunks for one message into ``messages_chunks``.

    Designed to be invoked from a hook in :class:`VectorWorker` or
    directly by main.py when the chunking feature flag is on. Idempotent:
    if a message already has chunks (same ``message_id``), they are
    deleted before re-inserting.
    """

    def __init__(self, db_path: str | Path, *, embedder: Any | None = None) -> None:
        self._db_path = Path(db_path)
        # 记忆系统升级 WI-M1.5：embedder 注入 → 每个 chunk 独立 embed 进
        # messages_chunks.embedding，召回端走 chunk 向量。None/mock → 不写
        # 向量（chunk 行仍落库，只是无向量、召回端跳过）。
        self._embedder = embedder

    async def _embed_chunks(self, chunks: list[str]) -> list[Optional[bytes]]:
        """对一批 chunk 文本算向量 bytes。无 embedder / mock / 失败 → 全 None。"""
        emb = self._embedder
        if emb is None:
            return [None] * len(chunks)
        try:
            if hasattr(emb, "is_mock") and emb.is_mock():
                return [None] * len(chunks)
            arr = await emb.encode(chunks)
        except Exception as exc:  # noqa: BLE001
            log.debug("MessageChunker embed failed: %s", exc)
            return [None] * len(chunks)
        try:
            import numpy as _np

            if arr is None or len(arr) != len(chunks):
                return [None] * len(chunks)
            return [
                _np.asarray(row, dtype=_np.float32).tobytes() for row in arr
            ]
        except Exception as exc:  # noqa: BLE001
            log.debug("MessageChunker embed serialize failed: %s", exc)
            return [None] * len(chunks)

    async def chunk_message(
        self,
        *,
        message_id: int,
        content: str,
    ) -> list[int]:
        """Split + persist. Returns list of new chunk row ids.

        For short messages we still emit a single row — that way the
        recall path can uniformly query ``messages_chunks`` without
        special-casing "no chunks → fall back to messages".
        """
        chunks = split_into_chunks(content)
        if not chunks:
            return []
        await ensure_memory_v2_tables(self._db_path)
        now = time.time()
        embeddings = await self._embed_chunks(chunks)  # WI-M1.5
        chunk_ids: list[int] = []
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute("PRAGMA busy_timeout=5000")
            # Idempotent re-chunk: drop old chunks first
            await conn.execute(
                "DELETE FROM messages_chunks WHERE message_id = ?",
                (int(message_id),),
            )
            for idx, ch in enumerate(chunks):
                cur = await conn.execute(
                    "INSERT INTO messages_chunks("
                    "message_id, chunk_index, text, embedding, created_at"
                    ") VALUES (?, ?, ?, ?, ?)",
                    (int(message_id), idx, ch, embeddings[idx], now),
                )
                chunk_ids.append(int(cur.lastrowid or 0))
                await cur.close()
            await conn.commit()
        return chunk_ids

    async def vector_search(
        self, query_embedding: Any, *, limit: int = 10
    ) -> list[dict[str, Any]]:
        """记忆系统升级 WI-M1.5：chunk 向量召回 → 返回 parent message。

        对所有带 ``embedding`` 的 chunk 做 brute-force cosine，按 parent
        ``message_id`` 去重（同一条消息多个 chunk 命中 → 取最高分那个），
        每行附 ``_score`` + 命中 chunk 的 ``text``。embedder mock 时 chunk
        无向量 → 返回空，调用方据此跳过 chunk 路。
        """
        if query_embedding is None:
            return []
        await ensure_memory_v2_tables(self._db_path)
        import numpy as np

        q = np.asarray(query_embedding, dtype=np.float32).ravel()
        q_norm = float(np.linalg.norm(q))
        if q_norm == 0.0:
            return []
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT message_id, text, embedding FROM messages_chunks "
                "WHERE embedding IS NOT NULL"
            )
            rows = await cur.fetchall()
            await cur.close()
        best: dict[int, dict[str, Any]] = {}
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
            mid = int(r["message_id"])
            cur_best = best.get(mid)
            if cur_best is None or cos > cur_best["_score"]:
                best[mid] = {
                    "message_id": mid,
                    "text": r["text"],
                    "_score": cos,
                }
        ranked = sorted(best.values(), key=lambda d: -d["_score"])
        return ranked[: int(limit)]

    async def chunks_for_message(self, message_id: int) -> list[dict[str, Any]]:
        await ensure_memory_v2_tables(self._db_path)
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT id, chunk_index, text FROM messages_chunks "
                "WHERE message_id = ? ORDER BY chunk_index",
                (int(message_id),),
            )
            rows = await cur.fetchall()
            await cur.close()
        return [dict(r) for r in rows]

    async def total_chunks(self) -> int:
        await ensure_memory_v2_tables(self._db_path)
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM messages_chunks")
            row = await cur.fetchone()
            await cur.close()
        return int(row[0]) if row else 0
