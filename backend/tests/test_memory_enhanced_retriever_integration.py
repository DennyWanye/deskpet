# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

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


# --- WI-2 回归：EnhancedRetriever 必须接受并透传 recency_half_life_days ----
#
# 根因（2026-06-19 真机 dev L3 偶发 TypeError "'>' not supported between
# instances of 'int' and 'dict'"）：cbdf855(task-scope-context-isolation)
# 给 base ``Retriever.recall`` + ``MemoryManager._safe_l3`` 调用加了
# ``recency_half_life_days`` 关键字，但漏给 ``EnhancedRetriever.recall``。
# 真机默认半衰期 7.0 + enhanced 召回器（rerank/chunk/embedder 插件需真
# embedder）→ manager 用该 kwarg 调 wrapper → wrapper 不认 → TypeError →
# manager 的 ``except TypeError`` 误判为「测试假替身签名」→ 退化重调
# ``recall(query, {policy dict})`` → dict 当 top_k → base ``max(dict, 20)``
# → int>dict → L3 整层降级。mock embedder 不复现（裸 base Retriever 认该
# kwarg，不走 wrapper）。


class _RecordingBase:
    """记录收到的 kwargs，签名与真实 base ``Retriever.recall`` 1:1。"""

    def __init__(self, hits: list[Hit]) -> None:
        self._hits = hits
        self.policy = SimpleNamespace(top_k=20)
        self.calls: list[dict] = []

    async def recall(
        self,
        query,  # noqa: ANN001
        top_k=None,  # noqa: ANN001
        *,
        cur_session_id=None,  # noqa: ANN001
        cur_session_kind=None,  # noqa: ANN001
        cross_session_decay=None,  # noqa: ANN001
        recency_half_life_days=None,  # noqa: ANN001
    ):
        self.calls.append(
            {
                "top_k": top_k,
                "cur_session_id": cur_session_id,
                "cur_session_kind": cur_session_kind,
                "cross_session_decay": cross_session_decay,
                "recency_half_life_days": recency_half_life_days,
            }
        )
        return list(self._hits)[: (top_k or self.policy.top_k)]


@pytest.mark.asyncio
async def test_enhanced_retriever_accepts_and_forwards_recency_half_life():
    """EnhancedRetriever.recall 收 recency_half_life_days 不抛，且原样透传给 base。"""
    base = _RecordingBase(
        [Hit(message_id=1, score=0.9, text="x", ts=0.0, source="vec")]
    )
    er = EnhancedRetriever(base)  # 裸 wrapper（无插件）

    # 红线：修复前这里直接 TypeError(unexpected keyword argument)。
    hits = await er.recall(
        "q",
        top_k=5,
        cur_session_id="default",
        cur_session_kind="companion",
        cross_session_decay=0.15,
        recency_half_life_days=7.0,
    )
    assert hits and hits[0].message_id == 1
    assert len(base.calls) == 1
    # 透传校验：base 收到的就是调用方传的值（不是 None / dict）。
    assert base.calls[0]["recency_half_life_days"] == 7.0
    assert base.calls[0]["cross_session_decay"] == 0.15
    assert base.calls[0]["cur_session_id"] == "default"
    # 关键：top_k 必须仍是 int，不能被退化路径换成 dict。
    assert base.calls[0]["top_k"] == 5


class _DictUnsafeBase(_RecordingBase):
    """复刻真实 base ``Retriever.recall`` 对 top_k 的处理：``max(top_k, policy.top_k)``。

    当 top_k 被退化路径换成 dict 时，``max(dict, 20)`` 触发 manager 日志里
    那个 ``'>' not supported between instances of 'int' and 'dict'``。
    用它作 base 就能在不依赖 sqlite-vec 的前提下端到端复现生产链路。
    """

    async def recall(self, query, top_k=None, **kw):  # noqa: ANN001
        effective = top_k if top_k is not None else self.policy.top_k
        # 真实 base recall 第一步：fanout_k = max(effective_top_k, policy.top_k)。
        # top_k 是 dict 时此处即 int>dict。
        max(effective, self.policy.top_k)
        return await super().recall(query, top_k=top_k, **kw)


@pytest.mark.asyncio
async def test_manager_l3_no_int_gt_dict_with_enhanced_retriever():
    """端到端复现：MemoryManager → EnhancedRetriever，L3 不得因 int>dict 降级。

    复刻生产接线：manager 默认 recency_half_life_days=7.0 + 召回器是
    EnhancedRetriever。修复前 → l3_failed(int>dict) → l3 空。修复后 → l3 命中。
    """
    from deskpet.memory.manager import MemoryManager

    base = _DictUnsafeBase(
        [Hit(message_id=42, score=0.9, text="记得CATL年报", ts=0.0, source="vec")]
    )
    er = EnhancedRetriever(base)
    mgr = MemoryManager(
        file_memory=object(),  # L1 未启用（policy 不含 snapshot）
        session_db=object(),   # L2 跳过（l2_top_k=0）
        retriever=er,
        recency_half_life_days=7.0,  # 真机默认值 —— 触发 bug 的关键
    )

    out = await mgr.recall(
        "我现在要做小学教育PPT",
        {"l2_top_k": 0, "l3_top_k": 5, "session_id": "default"},
    )
    # 修复前此处为 []（L3 整层降级）。
    assert out["l3"], "L3 不应因 int>dict 退化路径而空召回"
    assert out["l3"][0]["message_id"] == 42
    # 退化路径绝不能被触发：base 只应被调用一次，且 top_k 是 int。
    assert len(base.calls) == 1
    assert base.calls[0]["top_k"] == 5
    assert base.calls[0]["recency_half_life_days"] == 7.0
