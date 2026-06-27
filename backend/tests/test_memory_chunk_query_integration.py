# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""TG-5 — chunking + query rewriting 集成测试（WI-M1.5）。

chunker 是写入侧改造：长消息切块 + embed 进 messages_chunks；召回命中
返回 parent message。query_rewriter：短 query 入口被改写、长 query 不动。
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from deskpet.memory.retriever import Hit
from deskpet.memory.chunker import MessageChunker
from deskpet.memory.enhanced_retriever import EnhancedRetriever
from deskpet.memory.memory_v2_schema import _reset_cache_for_tests


@pytest.fixture
def db_path(tmp_path):
    _reset_cache_for_tests()
    return tmp_path / "state.db"


class _FakeEmbedder:
    """非 mock 确定性 embedder：所有文本投到同一维 → 互相 cosine=1。"""

    def is_mock(self) -> bool:
        return False

    async def encode(self, texts: list[str]) -> np.ndarray:
        rows = []
        for _t in texts:
            v = np.zeros(1024, dtype=np.float32)
            v[0] = 1.0
            rows.append(v)
        return np.stack(rows)


class _FakeBase:
    def __init__(self, hits: list[Hit]) -> None:
        self._hits = hits
        self.policy = SimpleNamespace(top_k=10)

    async def recall(self, query, top_k=None, **kw):  # noqa: ANN001
        return list(self._hits)[: (top_k or 10)]


# 长消息（>500 字、含句末标点）→ split_into_chunks 会切多块。
_LONG_MSG = "我今天研究了向量召回的实现细节。" * 45


# --- T5-1：chunking on → messages_chunks 多行 + 召回命中返回 parent ----
@pytest.mark.asyncio
async def test_t5_1_long_message_chunked_and_recalled(db_path):
    embedder = _FakeEmbedder()
    chunker = MessageChunker(db_path, embedder=embedder)
    ids = await chunker.chunk_message(message_id=42, content=_LONG_MSG)
    assert len(ids) > 1, "长消息应切成多块"
    assert await chunker.total_chunks() == len(ids)

    # 召回侧：EnhancedRetriever 带 chunk_store → 命中 chunk 折叠回 parent。
    er = EnhancedRetriever(
        _FakeBase([]), embedder=embedder, chunk_store=chunker,
    )
    hits = await er.recall("向量召回", top_k=5)
    chunk_hits = [h for h in hits if h.source == "chunk"]
    assert chunk_hits and chunk_hits[0].message_id == 42


# --- T5-2：chunking off → chunk_store=None → 无 chunk 召回 -------------
@pytest.mark.asyncio
async def test_t5_2_chunking_off_no_chunk_recall(db_path):
    embedder = _FakeEmbedder()
    base_hit = Hit(message_id=1, score=0.5, text="base", ts=0.0, source="vec")
    er = EnhancedRetriever(_FakeBase([base_hit]), embedder=embedder)
    assert er._chunk_store is None
    hits = await er.recall("向量召回", top_k=5)
    assert all(h.source != "chunk" for h in hits)


# --- T5-3：chunk backfill 把历史长消息切块 ------------------------------
@pytest.mark.asyncio
async def test_t5_3_chunk_backfill_idempotent(db_path):
    embedder = _FakeEmbedder()
    chunker = MessageChunker(db_path, embedder=embedder)
    n1 = await chunker.chunk_message(message_id=7, content=_LONG_MSG)
    # 重切（backfill 可重入）—— 先删后插，不翻倍。
    n2 = await chunker.chunk_message(message_id=7, content=_LONG_MSG)
    assert len(n1) == len(n2)
    assert await chunker.total_chunks() == len(n2)


# --- T5-4 / T5-5：query rewriter 只改写短 query --------------------------
class _CountingRewriter:
    def __init__(self) -> None:
        self.calls = 0

    async def rewrite(self, query: str, *, context: str = "") -> str:
        self.calls += 1
        return query + "（已扩写）"


@pytest.mark.asyncio
async def test_t5_4_short_query_is_rewritten():
    rw = _CountingRewriter()
    er = EnhancedRetriever(_FakeBase([]), query_rewriter=rw)
    await er.recall("零食", top_k=3)  # 2 字，短 query
    assert rw.calls == 1


@pytest.mark.asyncio
async def test_t5_5_long_query_not_rewritten():
    rw = _CountingRewriter()
    er = EnhancedRetriever(_FakeBase([]), query_rewriter=rw)
    # >= 20 字的长 query → 不触发改写。
    await er.recall("我想知道之前我们详细讨论过的那个关于零食推荐的事情", top_k=3)
    assert rw.calls == 0
