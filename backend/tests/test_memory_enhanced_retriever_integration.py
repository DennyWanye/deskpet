"""TG-4 — EnhancedRetriever 接管召回集成测试（WI-M1.4）。

验证 facts 经**向量召回**进 RRF 结果、合成 Hit 文本能渲染进 prompt、
flag 切换召回器、facts_weight 显式传 0.2、facts 表空时不报错。
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from deskpet.memory.retriever import Hit
from deskpet.memory.facts import FactsStore
from deskpet.memory.enhanced_retriever import (
    EnhancedRetriever,
    build_recall_retriever,
)
from deskpet.memory.memory_v2_schema import _reset_cache_for_tests
from deskpet.agent.assembler.components.memory import _render_l3_only


@pytest.fixture
def db_path(tmp_path):
    _reset_cache_for_tests()
    return tmp_path / "state.db"


class _FakeEmbedder:
    """非 mock 的确定性 embedder：把文本投到「食物簇 / 其它簇」两维。

    含 花生/过敏/零食/吃/食物 关键字 → dim0；否则 → dim1。这样
    「花生过敏」fact 与「我能吃什么零食」query 落同簇 → cosine=1 命中，
    用来确定性地验证 facts 向量召回 plumbing（语义质量靠真模型，不在此测）。
    """

    _FOOD = ("花生", "过敏", "零食", "吃", "食物")

    def is_mock(self) -> bool:
        return False

    async def encode(self, texts: list[str]) -> np.ndarray:
        rows = []
        for t in texts:
            v = np.zeros(1024, dtype=np.float32)
            v[0 if any(k in t for k in self._FOOD) else 1] = 1.0
            rows.append(v)
        return np.stack(rows)


class _FakeBase:
    def __init__(self, hits: list[Hit]) -> None:
        self._hits = hits
        self.policy = SimpleNamespace(top_k=10)

    async def recall(self, query, top_k=None, **kw):  # noqa: ANN001
        return list(self._hits)[: (top_k or 10)]


# --- T4-1 / T4-5 ---------------------------------------------------------
def test_t4_1_enhanced_flag_wraps_with_facts_weight_02():
    base = _FakeBase([])
    r = build_recall_retriever(
        base, rerank=False, enhanced_retriever=True,
        query_rewrite=False, chunking=False,
        facts_store=object(), facts_weight=0.2,
    )
    assert isinstance(r, EnhancedRetriever)
    # T4-5：facts_weight 必须显式传 0.2，否则默认 0.0 facts 永不进结果。
    assert r._facts_weight == 0.2
    assert r._facts_store is not None


# --- T4-2 ----------------------------------------------------------------
@pytest.mark.asyncio
async def test_t4_2_all_flags_off_returns_bare_base():
    base = _FakeBase([Hit(message_id=9, score=0.5, text="x", ts=0.0, source="vec")])
    r = build_recall_retriever(
        base, rerank=False, enhanced_retriever=False,
        query_rewrite=False, chunking=False,
    )
    assert r is base
    # recall 与第一代字节级一致（同一对象，同一结果）。
    assert await r.recall("q") == await base.recall("q")


# --- T4-3：facts 经向量召回命中 -----------------------------------------
@pytest.mark.asyncio
async def test_t4_3_facts_recalled_via_vector_search(db_path):
    embedder = _FakeEmbedder()
    store = FactsStore(db_path, embedder=embedder)
    await store.upsert(
        category="preference", subject="user", key="food_allergy",
        value="花生过敏", confidence=0.9, source_msg_id=1, evidence="我对花生过敏",
    )
    er = EnhancedRetriever(
        _FakeBase([]), facts_store=store, facts_weight=0.2, embedder=embedder,
    )
    # query 落「食物簇」→ 向量召回命中 facts。
    hits = await er.recall("我能吃什么零食", top_k=5)
    fact_hits = [h for h in hits if h.source == "facts"]
    assert len(fact_hits) == 1
    assert "花生" in fact_hits[0].text


# --- T4-4：facts 命中文本渲染进 prompt 段 -------------------------------
@pytest.mark.asyncio
async def test_t4_4_fact_hit_renders_into_prompt(db_path):
    embedder = _FakeEmbedder()
    store = FactsStore(db_path, embedder=embedder)
    await store.upsert(
        category="preference", subject="user", key="food_allergy",
        value="花生过敏", confidence=0.9, source_msg_id=1, evidence="我对花生过敏",
    )
    er = EnhancedRetriever(
        _FakeBase([]), facts_store=store, facts_weight=0.2, embedder=embedder,
    )
    hits = await er.recall("我能吃什么零食", top_k=5)
    # 模拟 MemoryManager._safe_l3 经 _to_dict 透传 → MemoryComponent 渲染。
    l3 = [
        {"text": h.text, "score": h.score, "source": h.source}
        for h in hits
    ]
    rendered = _render_l3_only(l3)
    assert "[fact]" in rendered and "花生" in rendered


# --- T4-6：enhanced on 但 facts 表空 → 不报错 ----------------------------
@pytest.mark.asyncio
async def test_t4_6_empty_facts_table_no_error(db_path):
    embedder = _FakeEmbedder()
    store = FactsStore(db_path, embedder=embedder)
    base_hit = Hit(message_id=1, score=0.5, text="基础召回", ts=0.0, source="vec")
    er = EnhancedRetriever(
        _FakeBase([base_hit]), facts_store=store,
        facts_weight=0.2, embedder=embedder,
    )
    hits = await er.recall("随便问点什么", top_k=5)
    # 退化为老 RRF：base hit 仍在，无 facts。
    assert any(h.message_id == 1 for h in hits)
    assert all(h.source != "facts" for h in hits)
