"""TG-3 — reranker 接入集成测试（WI-M1.3）。

验证 EnhancedRetriever 在 RRF 之后插 reranker；mock reranker 自动 bypass
（hash 打分会打乱召回顺序 → 绝不污染线上）；flag 切换召回器类型。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from deskpet.memory.retriever import Hit
from deskpet.memory.reranker import MockReranker
from deskpet.memory.enhanced_retriever import (
    EnhancedRetriever,
    build_recall_retriever,
)


class _FakeBase:
    """假 base Retriever：recall 返回固定 Hit 列表。"""

    def __init__(self, hits: list[Hit]) -> None:
        self._hits = hits
        self.policy = SimpleNamespace(top_k=10)

    async def recall(self, query, top_k=None, **kw):  # noqa: ANN001
        return list(self._hits)[: (top_k or 10)]


class _FakeRealReranker:
    """非 mock reranker：把候选顺序反转，用于验证「非 mock → 真重排」。"""

    def is_mock(self) -> bool:
        return False

    async def rerank(self, query, candidates, *, top_k=None):  # noqa: ANN001
        out = [(int(c["message_id"]), float(i)) for i, c in enumerate(candidates)]
        out.reverse()
        return out


def _hits() -> list[Hit]:
    return [
        Hit(message_id=1, score=0.9, text="第一条记忆", ts=0.0, source="vec"),
        Hit(message_id=2, score=0.8, text="第二条记忆", ts=0.0, source="vec"),
        Hit(message_id=3, score=0.7, text="第三条记忆", ts=0.0, source="vec"),
    ]


# --- T3-1：真实（非 mock）reranker → 确实重排 -------------------------
@pytest.mark.asyncio
async def test_t3_1_real_reranker_reorders():
    er = EnhancedRetriever(_FakeBase(_hits()), reranker=_FakeRealReranker())
    out = await er.recall("query", top_k=3)
    # _FakeRealReranker 反转顺序 → [3,2,1]
    assert [h.message_id for h in out] == [3, 2, 1]


# --- T3-2：mock reranker → 自动 bypass，顺序 = RRF 原序 ----------------
@pytest.mark.asyncio
async def test_t3_2_mock_reranker_bypassed():
    er = EnhancedRetriever(_FakeBase(_hits()), reranker=MockReranker())
    out = await er.recall("query", top_k=3)
    # mock 被 bypass → 保持 base RRF 原序 [1,2,3]
    assert [h.message_id for h in out] == [1, 2, 3]
    assert er._rerank_mock_warned is True  # warn 过一次


# --- T3-3：rerank off → build_recall_retriever 返回裸 base -------------
def test_t3_3_rerank_off_returns_bare_base():
    base = _FakeBase(_hits())
    r = build_recall_retriever(
        base, rerank=False, enhanced_retriever=False,
        query_rewrite=False, chunking=False,
    )
    assert r is base


# --- T3-4：rerank on → EnhancedRetriever，facts_weight=0 ---------------
def test_t3_4_rerank_on_wraps_with_facts_weight_zero():
    base = _FakeBase(_hits())
    r = build_recall_retriever(
        base, rerank=True, enhanced_retriever=False,
        query_rewrite=False, chunking=False,
        reranker=MockReranker(),
    )
    assert isinstance(r, EnhancedRetriever)
    assert r._facts_weight == 0.0  # rerank-only：facts 路关闭
    assert r._reranker is not None
