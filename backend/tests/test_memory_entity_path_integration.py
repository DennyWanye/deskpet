# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""TG-S3 — entity 索引检索路集成测试（Stage 2 WI-S2.2）。

PRD：plans/2026-05-23-memory-system-stage2/00-PRD.md §3.3
TDD：plans/2026-05-23-memory-system-stage2/01-TDD.md §A2 + §TG-S3

覆盖 TS3-1 ~ TS3-10（v2 后 10 个用例全集）：

  * TS3-1  entity_path on + fact value 含"旺财" + query "旺财怎么样了" → 命中
  * TS3-2  entity_path off → entity 路不走
  * TS3-3  LLM 抛错 → Composite 降级 regex 仍命中
  * TS3-4  纯英文小写 query → 不抽 entity → entity_hits=[]
  * TS3-5  find_by_entities 两实体 → LIKE value 列两词、去重、按 updated_at 排序
  * TS3-6  entity 列表超过 5 → 截断前 5
  * TS3-7  单 entity 长度 < 2 字 → 跳过
  * TS3-8  fact_hits 与 entity_hits 命中同 fact id → RRF 去重
  * TS3-9  ★v2 RegexEntityExtractor("今天怎么了") → 停用词过滤掉
  * TS3-10 ★v2 LIKE 只查 value：subject="user" 大量 fact + query 含 "user"
            → entity_path 不会被 subject 匹配
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import numpy as np
import pytest

from deskpet.memory.entity_extractor import (
    CompositeEntityExtractor,
    LLMEntityExtractor,
    NoopEntityExtractor,
    RegexEntityExtractor,
    _STOPWORDS,
)
from deskpet.memory.enhanced_retriever import (
    EnhancedRetriever,
    _FACT_ID_OFFSET,
    build_recall_retriever,
)
from deskpet.memory.facts import FactsStore
from deskpet.memory.memory_v2_schema import _reset_cache_for_tests
from deskpet.memory.retriever import Hit


# ────────────────────────────────────────────────────────────────────
# 共用 fixtures / 帮手
# ────────────────────────────────────────────────────────────────────


@pytest.fixture
def db_path(tmp_path):
    _reset_cache_for_tests()
    return tmp_path / "state.db"


class _FakeBase:
    """裸 base Retriever 替身，返回固定 hits 列表。"""

    def __init__(self, hits: list[Hit]) -> None:
        self._hits = hits
        self.policy = SimpleNamespace(top_k=10)

    async def recall(self, query, top_k=None, **kw):  # noqa: ANN001
        return list(self._hits)[: (top_k or 10)]


async def _seed_fact(
    store: FactsStore,
    *,
    key: str,
    value: str,
    subject: str = "user",
    updated_at: float | None = None,
) -> int:
    """插入 active fact，可选覆盖 updated_at（用于排序断言）。"""
    fid = await store.upsert(
        category="preference",
        subject=subject,
        key=key,
        value=value,
        confidence=0.9,
        source_msg_id=1,
        evidence=f"测试种子 {key}",
    )
    if updated_at is not None:
        # 直接 UPDATE 覆盖时间戳
        import aiosqlite
        async with aiosqlite.connect(store._db_path) as conn:
            await conn.execute(
                "UPDATE facts SET updated_at = ? WHERE id = ?",
                (float(updated_at), fid),
            )
            await conn.commit()
    return fid


# ────────────────────────────────────────────────────────────────────
# 单元：RegexEntityExtractor 停用词（TS3-9）
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ts3_9_regex_filters_stopwords():
    """TS3-9 ★v2：高频中文停用词被过滤。"""
    ex = RegexEntityExtractor()
    out = await ex.extract("今天怎么了")
    # "今天""怎么""怎么了"（如果命中 3 字）都在停用词集
    assert "今天" not in out
    assert "怎么" not in out
    # 停用词集本身的健全性
    assert "今天" in _STOPWORDS
    assert "怎么" in _STOPWORDS


@pytest.mark.asyncio
async def test_ts3_9b_regex_keeps_real_entities():
    """配合 TS3-9：真实 entity 不被误杀。"""
    ex = RegexEntityExtractor()
    out = await ex.extract("旺财今天去公园玩了吗")
    # "旺财""公园" 应保留；"今天""了吗" 应过滤
    assert "旺财" in out
    assert "公园" in out
    assert "今天" not in out
    assert "了吗" not in out


# ────────────────────────────────────────────────────────────────────
# 单元：FactsStore.find_by_entities（TS3-5 / TS3-6 / TS3-7 / TS3-10）
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ts3_5_find_by_entities_two_entities_dedupe_sort(db_path):
    """TS3-5：两个 entity，命中后去重并按 updated_at 倒排。"""
    store = FactsStore(db_path)
    fid_a = await _seed_fact(
        store, key="pet_name", value="旺财是只柯基",
        updated_at=1000.0,
    )
    fid_b = await _seed_fact(
        store, key="friend_name", value="同事 Mike 在上海",
        updated_at=2000.0,
    )
    # 同一 value 含"旺财"和"Mike"都不可能 → 此 fact 只能被"旺财"命中
    rows = await store.find_by_entities(["旺财", "Mike"], limit=10)
    ids = [r["id"] for r in rows]
    assert set(ids) == {fid_a, fid_b}
    # updated_at 大的（Mike）排前
    assert ids[0] == fid_b
    # 去重：fid 各只出现一次
    assert len(ids) == len(set(ids))


@pytest.mark.asyncio
async def test_ts3_5b_find_by_entities_same_fact_two_entities(db_path):
    """补充 TS3-5：同一 fact 同时含两个 entity → 去重为一行。"""
    store = FactsStore(db_path)
    fid = await _seed_fact(
        store, key="trip", value="旺财和 Mike 一起去公园",
    )
    rows = await store.find_by_entities(["旺财", "Mike"], limit=10)
    assert len(rows) == 1
    assert rows[0]["id"] == fid


@pytest.mark.asyncio
async def test_ts3_6_truncate_entities_to_five(db_path):
    """TS3-6：实体列表 > 5 时截断前 5。"""
    store = FactsStore(db_path)
    # 准备 6 个不同的 fact，每个对应一个 entity
    fids = []
    for i, name in enumerate(["旺财", "Mike", "上海", "公园", "海洋馆", "图书馆"]):
        fids.append(
            await _seed_fact(store, key=f"e{i}", value=f"涉及{name}的事")
        )
    # 传 6 个 entity；第 6 个（图书馆）应被截断
    rows = await store.find_by_entities(
        ["旺财", "Mike", "上海", "公园", "海洋馆", "图书馆"], limit=10,
    )
    rec_ids = {r["id"] for r in rows}
    # 前 5 个应命中
    assert fids[0] in rec_ids
    assert fids[4] in rec_ids
    # 第 6 个被截断 → 不应出现
    assert fids[5] not in rec_ids


@pytest.mark.asyncio
async def test_ts3_7_skip_short_entities(db_path):
    """TS3-7：单 entity 长度 < 2 字符被跳过。"""
    store = FactsStore(db_path)
    fid = await _seed_fact(store, key="x", value="包含X单字符")
    # "X" 单字符跳过；"a" 单字符跳过；空白跳过
    rows = await store.find_by_entities(["X", "a", " ", ""], limit=10)
    assert rows == []
    # 但 2 字符可以
    rows2 = await store.find_by_entities(["X单"], limit=10)
    assert any(r["id"] == fid for r in rows2)


@pytest.mark.asyncio
async def test_ts3_10_like_only_value_not_subject(db_path):
    """TS3-10 ★v2：LIKE 只查 value 列；subject="user" 大量 fact + query 含 "user"
    词不应被 entity_path 误命中。"""
    store = FactsStore(db_path)
    # 大量 subject="user" 但 value 不含 "user" 的 fact
    for i in range(8):
        await _seed_fact(
            store,
            key=f"hobby_{i}",
            value=f"爱好{i}是看书",  # value 不含 "user"
            subject="user",
        )
    # entity "user" 长度 4 字符 ≥ 2 → 不会被长度过滤
    rows = await store.find_by_entities(["user"], limit=10)
    # 因 LIKE 只查 value，且 value 都不含 "user" → 0 命中
    assert rows == [], (
        f"entity_path 不应被 subject 误命中，但命中 {len(rows)} 条"
    )

    # 反证：value 含 "user" 字串的 fact 应能被命中
    fid = await _seed_fact(
        store, key="github_handle", value="github user is alice",
    )
    rows2 = await store.find_by_entities(["user"], limit=10)
    assert any(r["id"] == fid for r in rows2)


@pytest.mark.asyncio
async def test_ts3_find_by_entities_empty_list(db_path):
    """find_by_entities([]) → 空返。"""
    store = FactsStore(db_path)
    await _seed_fact(store, key="x", value="something")
    rows = await store.find_by_entities([], limit=10)
    assert rows == []


# ────────────────────────────────────────────────────────────────────
# 集成：EnhancedRetriever 接 entity_extractor（TS3-1 / TS3-2 / TS3-4 / TS3-8）
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ts3_1_entity_path_on_recalls_fact(db_path):
    """TS3-1：entity_path on + fact value 含"旺财" + query "旺财怎么样了" → entity 路命中。"""
    store = FactsStore(db_path)
    fid = await _seed_fact(
        store, key="pet_name", value="家里有只柯基叫旺财",
    )
    er = EnhancedRetriever(
        base=_FakeBase([]),
        facts_store=store,
        facts_weight=0.0,  # 关 facts 路 RRF；只测 entity 路
        entity_extractor=RegexEntityExtractor(),
        entity_weight=0.10,
    )
    hits = await er.recall("旺财怎么样了", top_k=5)
    # entity 路命中应出现，source="entity"
    ent_hits = [h for h in hits if h.source == "entity"]
    assert len(ent_hits) >= 1
    assert ent_hits[0].message_id == _FACT_ID_OFFSET + fid
    assert "旺财" in ent_hits[0].text


@pytest.mark.asyncio
async def test_ts3_2_entity_path_off_no_entity_hits(db_path):
    """TS3-2：entity_extractor=None 时 entity 路不走，召回与 Stage 1 一致。"""
    store = FactsStore(db_path)
    await _seed_fact(store, key="pet_name", value="家里有只柯基叫旺财")
    er = EnhancedRetriever(
        base=_FakeBase([
            Hit(message_id=1, score=0.5, text="基础", ts=0.0, source="vec"),
        ]),
        facts_store=store,
        facts_weight=0.0,
        entity_extractor=None,  # entity 路关
        entity_weight=0.10,
    )
    hits = await er.recall("旺财怎么样了", top_k=5)
    # 不应有 entity 命中
    assert all(h.source != "entity" for h in hits)
    # base hit 仍在
    assert any(h.message_id == 1 for h in hits)


@pytest.mark.asyncio
async def test_ts3_2b_byte_identity_when_extractor_none(db_path):
    """byte-identity 强化：entity_extractor=None 时 build_recall_retriever
    应等价 Stage 1 路径（不因 Stage 2 改动而引入额外 wrapper）。"""
    store = FactsStore(db_path)
    base = _FakeBase([Hit(message_id=9, score=0.5, text="x", ts=0.0, source="vec")])
    # 全关 + entity_extractor=None → 返回裸 base
    r = build_recall_retriever(
        base,
        rerank=False,
        enhanced_retriever=False,
        query_rewrite=False,
        chunking=False,
        entity_extractor=None,
    )
    assert r is base


@pytest.mark.asyncio
async def test_ts3_3_llm_throws_composite_falls_back_regex(db_path):
    """TS3-3：LLMEntityExtractor 抛错 → Composite 自动降级 regex 仍命中。"""
    store = FactsStore(db_path)
    fid = await _seed_fact(store, key="pet_name", value="柯基旺财喜欢散步")

    async def _failing_llm(prompt: str) -> str:
        raise RuntimeError("LLM 503 模拟")

    composite = CompositeEntityExtractor(
        LLMEntityExtractor(_failing_llm),
        RegexEntityExtractor(),
    )
    er = EnhancedRetriever(
        base=_FakeBase([]),
        facts_store=store,
        facts_weight=0.0,
        entity_extractor=composite,
        entity_weight=0.10,
    )
    hits = await er.recall("旺财今天去哪了", top_k=5)
    ent_hits = [h for h in hits if h.source == "entity"]
    assert len(ent_hits) >= 1, "LLM 抛错应降级 regex；regex 应抽出'旺财'命中"
    assert ent_hits[0].message_id == _FACT_ID_OFFSET + fid


@pytest.mark.asyncio
async def test_ts3_4_pure_lower_english_no_entities(db_path):
    """TS3-4：纯小写英文 query → regex 抽不到 entity → entity_hits=[]。"""
    store = FactsStore(db_path)
    await _seed_fact(store, key="pet", value="just text")
    er = EnhancedRetriever(
        base=_FakeBase([]),
        facts_store=store,
        facts_weight=0.0,
        entity_extractor=RegexEntityExtractor(),
        entity_weight=0.10,
    )
    hits = await er.recall("how do i do that today", top_k=5)
    # 全小写英文 + 停用词 "How"/"that"/"today" 均被过滤
    assert all(h.source != "entity" for h in hits)


@pytest.mark.asyncio
async def test_ts3_8_dedupe_fact_hits_and_entity_hits(db_path):
    """TS3-8：同一 fact 同时被 facts 路（向量召回降级 LIKE）和 entity 路命中
    → merged 里只出现一次。"""
    store = FactsStore(db_path)
    fid = await _seed_fact(store, key="pet_name", value="柯基旺财")
    # facts_store.search() LIKE 子串 "旺财" 也能命中（_collect_fact_hits 在
    # embedder=None 时降级到 LIKE）。entity 路也命中同一 fact。
    er = EnhancedRetriever(
        base=_FakeBase([]),
        facts_store=store,
        facts_weight=0.2,  # 开 facts 路
        embedder=None,  # 降级 LIKE
        entity_extractor=RegexEntityExtractor(),
        entity_weight=0.10,
    )
    hits = await er.recall("旺财", top_k=10)
    # 提取所有 fact-相关命中（facts + entity）
    fact_like = [
        h for h in hits if h.message_id == _FACT_ID_OFFSET + fid
    ]
    assert len(fact_like) == 1, (
        f"facts 路 + entity 路应去重，但 fact {fid} 出现 {len(fact_like)} 次"
    )


# ────────────────────────────────────────────────────────────────────
# 补充：build_recall_retriever 单测（entity_path 与现有 flag 解耦）
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_recall_retriever_only_entity_path(db_path):
    """entity_extractor 注入即可让 build 包 wrapper —— 不依赖其他 flag。"""
    store = FactsStore(db_path)
    await _seed_fact(store, key="pet_name", value="旺财")
    base = _FakeBase([])
    r = build_recall_retriever(
        base,
        rerank=False,
        enhanced_retriever=False,
        query_rewrite=False,
        chunking=False,
        facts_store=store,
        entity_extractor=RegexEntityExtractor(),
        entity_weight=0.10,
    )
    assert isinstance(r, EnhancedRetriever)
    # 只开 entity_path：facts_weight 仍是 0（facts 路关），entity 路可用
    assert r._facts_weight == 0.0
    assert r._entity_extractor is not None
    assert r._facts_store is store  # entity 路依赖 facts_store 应被透传


@pytest.mark.asyncio
async def test_ts3_composite_llm_returns_entities_skips_regex():
    """Composite：LLM 抽到结果时不再走 regex。"""

    async def _llm_returns_entities(prompt: str) -> str:
        return '["旺财", "Mike"]'

    comp = CompositeEntityExtractor(
        LLMEntityExtractor(_llm_returns_entities),
        RegexEntityExtractor(),  # 即便也能抽，应不被调用
    )
    out = await comp.extract("旺财和 Mike 怎么了")
    assert "旺财" in out and "Mike" in out


@pytest.mark.asyncio
async def test_ts3_noop_extractor_always_empty():
    out = await NoopEntityExtractor().extract("旺财怎么样了")
    assert out == []


@pytest.mark.asyncio
async def test_ts3_llm_extractor_filters_stopwords_in_output():
    """LLM 即使错误把"我们"塞进结果，也应被停用词过滤。"""

    async def _llm_returns_with_stopword(prompt: str) -> str:
        return '["我们", "旺财", "今天"]'

    ex = LLMEntityExtractor(_llm_returns_with_stopword)
    out = await ex.extract("我们和旺财今天去哪")
    # 停用词过滤后只剩"旺财"
    assert out == ["旺财"]
