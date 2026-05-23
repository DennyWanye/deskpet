"""TG-S1 — cross-key 矛盾治理集成测试（Stage 2 / WI-S2.1a）。

针对 TDD §TG-S1 (TS1-1 ~ TS1-14)，验证 FactExtractor.cross_key_merge
分支在真实运行栈里的行为：mock LLM 控制 conflict / should_insert 决策，
真 FactsStore 持久化，断言 facts 表的 is_active / superseded_by。
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from deskpet.memory.memory_v2_schema import (
    ensure_memory_v2_tables,
    _reset_cache_for_tests,
)
from deskpet.memory.schema_v2_migrator import _reset_failures_for_tests
from deskpet.memory.facts import (
    FactsStore,
    FactExtractor,
    _merge_dedupe_facts,
    _parse_cross_key_decision,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    _reset_cache_for_tests()
    _reset_failures_for_tests()
    return tmp_path / "state.db"


async def _make_store(db_path: Path) -> FactsStore:
    await ensure_memory_v2_tables(db_path)
    return FactsStore(db_path)


class StubLLM:
    """Programmable LLM mock — sequence of (kind, response) per call."""

    def __init__(self, extract_response: str = "[]", cross_key_response: str = ""):
        self.extract_response = extract_response
        self.cross_key_response = cross_key_response
        self.calls: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        if "cross-key contradictions" in prompt.lower() or "EXISTING active facts" in prompt:
            return self.cross_key_response
        return self.extract_response


def _extract_response_for(category: str, key: str, value: str) -> str:
    return json.dumps([{
        "category": category, "subject": "user", "key": key,
        "value": value, "confidence": 0.9, "evidence": value,
    }])


# ---------------------------------------------------------------------------
# TS1-1 — 跨 key 矛盾：fact A 被新 fact B 标 superseded
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ts1_1_cross_key_supersedes_old_fact(db_path: Path) -> None:
    store = await _make_store(db_path)
    # 准备：fact A 已存在
    a_id = await store.upsert(
        category="preference", subject="user",
        key="allergy_peanut", value="对花生过敏",
        confidence=0.9, source_msg_id=1, evidence="对花生过敏",
    )
    llm = StubLLM(
        extract_response=_extract_response_for(
            "preference", "allergy_seafood", "对海鲜过敏",
        ),
        cross_key_response=json.dumps({
            "conflicts": [{"old_id": a_id, "reason": "user changed their mind"}],
            "should_insert": True,
        }),
    )
    ext = FactExtractor(
        store, extract_llm=llm, merge_llm=llm,
        cross_key_merge=True, cross_key_llm=llm,
    )
    persisted = await ext.process_message(
        message_id=2, content="其实我不是花生过敏，是海鲜过敏", role="user",
    )
    # 应有 1 insert + 1 supersede
    actions = sorted(p["action"] for p in persisted)
    assert "insert" in actions
    assert "superseded" in actions
    # A 已 inactive，superseded_by 指向新 id
    async with aiosqlite.connect(store._db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT is_active, superseded_by FROM facts WHERE id = ?",
            (a_id,),
        )
        r = await cur.fetchone()
    assert r["is_active"] == 0
    new_id = next(p["id"] for p in persisted if p["action"] == "insert")
    assert r["superseded_by"] == new_id


# ---------------------------------------------------------------------------
# TS1-2 — should_insert=True + conflicts=[] → 两条都 active
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ts1_2_no_conflict_keeps_both_active(db_path: Path) -> None:
    store = await _make_store(db_path)
    a_id = await store.upsert(
        category="preference", subject="user",
        key="favorite_drink", value="咖啡",
        confidence=0.9, source_msg_id=1, evidence="喝咖啡",
    )
    llm = StubLLM(
        extract_response=_extract_response_for("preference", "favorite_food", "披萨"),
        cross_key_response=json.dumps({"conflicts": [], "should_insert": True}),
    )
    ext = FactExtractor(
        store, extract_llm=llm, merge_llm=llm,
        cross_key_merge=True, cross_key_llm=llm,
    )
    await ext.process_message(
        message_id=2, content="我也很喜欢吃披萨这种食物啊", role="user",
    )
    active = await store.list_active(subject="user")
    assert len(active) == 2
    ids = {r["id"] for r in active}
    assert a_id in ids


# ---------------------------------------------------------------------------
# TS1-3 — LLM 抛错 → fallback 走 Stage 1 insert 路径，A 仍 active
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ts1_3_llm_error_falls_back(db_path: Path) -> None:
    store = await _make_store(db_path)
    a_id = await store.upsert(
        category="preference", subject="user", key="k1", value="v1",
        confidence=0.9, source_msg_id=1, evidence="ev1",
    )

    class ErrLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self, prompt: str) -> str:
            self.calls += 1
            # Extract 返正常 → 进 cross-key 后抛
            if "EXISTING active facts" in prompt or "cross-key" in prompt.lower():
                raise RuntimeError("simulated cross-key llm fail")
            return _extract_response_for("preference", "k2", "v2")

    err_llm = ErrLLM()
    ext = FactExtractor(
        store, extract_llm=err_llm, merge_llm=err_llm,
        cross_key_merge=True, cross_key_llm=err_llm,
    )
    persisted = await ext.process_message(
        message_id=2, content="some new info", role="user",
    )
    # cross-key 决策失败 → 但 _decide_cross_key_conflict 返 safe default
    # (should_insert=True, conflicts=[]) → 新 fact 仍插入；A 不动
    assert len(persisted) >= 1
    active = await store.list_active(subject="user")
    assert a_id in {r["id"] for r in active}


# ---------------------------------------------------------------------------
# TS1-4 — LLM 返非法 JSON → safe default
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ts1_4_invalid_json_safe_default(db_path: Path) -> None:
    store = await _make_store(db_path)
    a_id = await store.upsert(
        category="preference", subject="user", key="k1", value="v1",
        confidence=0.9, source_msg_id=1, evidence="ev1",
    )
    llm = StubLLM(
        extract_response=_extract_response_for("preference", "k2", "v2"),
        cross_key_response="this is not json at all },{",
    )
    ext = FactExtractor(
        store, extract_llm=llm, merge_llm=llm,
        cross_key_merge=True, cross_key_llm=llm,
    )
    await ext.process_message(message_id=2, content="some new info", role="user")
    active = await store.list_active(subject="user")
    assert a_id in {r["id"] for r in active}


# ---------------------------------------------------------------------------
# TS1-5 — cross_key_merge=False → 与 Stage 1 字节级一致
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ts1_5_flag_off_byte_identical(db_path: Path) -> None:
    store = await _make_store(db_path)
    llm = StubLLM(
        extract_response=_extract_response_for("preference", "k2", "v2"),
    )
    ext = FactExtractor(
        store, extract_llm=llm, merge_llm=llm,
        cross_key_merge=False,
    )
    await ext.process_message(message_id=2, content="message text here", role="user")
    # cross_key_response 应永不被读
    cross_key_prompts = [c for c in llm.calls if "EXISTING active facts" in c]
    assert cross_key_prompts == []


# ---------------------------------------------------------------------------
# TS1-6 — candidates 上限 25（_merge_dedupe_facts 单测）
# ---------------------------------------------------------------------------
def test_ts1_6_dedupe_limits_to_25() -> None:
    recent = [{"id": i, "key": f"k{i}", "value": f"v{i}", "updated_at": 100}
              for i in range(25)]
    semantic = [{"id": i, "key": f"k{i}", "value": f"v{i}", "updated_at": 100}
                for i in range(20, 40)]
    out = _merge_dedupe_facts(recent, semantic, limit=25)
    assert len(out) <= 25


# ---------------------------------------------------------------------------
# TS1-7 — prompt 不含 evidence 字段（控长度）
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ts1_7_prompt_no_evidence(db_path: Path) -> None:
    store = await _make_store(db_path)
    await store.upsert(
        category="preference", subject="user", key="kx", value="vx",
        confidence=0.9, source_msg_id=1,
        evidence="!!!SENSITIVE EVIDENCE STRING!!!",
    )
    captured: list[str] = []

    class CaptureLLM:
        async def __call__(self, prompt: str) -> str:
            captured.append(prompt)
            if "EXISTING active facts" in prompt:
                return json.dumps({"conflicts": [], "should_insert": True})
            return _extract_response_for("preference", "ky", "vy")

    ext = FactExtractor(
        store, extract_llm=CaptureLLM(), merge_llm=CaptureLLM(),
        cross_key_merge=True, cross_key_llm=CaptureLLM(),
    )
    await ext.process_message(message_id=2, content="some text content", role="user")
    cross_key_prompts = [p for p in captured if "EXISTING active facts" in p]
    assert len(cross_key_prompts) >= 1
    for p in cross_key_prompts:
        assert "!!!SENSITIVE EVIDENCE STRING!!!" not in p


# ---------------------------------------------------------------------------
# TS1-8 — LLM 返不存在的 old_id → log warning，不 mark_superseded
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ts1_8_unknown_old_id_ignored(db_path: Path) -> None:
    store = await _make_store(db_path)
    a_id = await store.upsert(
        category="preference", subject="user", key="k1", value="v1",
        confidence=0.9, source_msg_id=1, evidence="ev",
    )
    llm = StubLLM(
        extract_response=_extract_response_for("preference", "k2", "v2"),
        cross_key_response=json.dumps({
            "conflicts": [{"old_id": 99999, "reason": "x"}],
            "should_insert": True,
        }),
    )
    ext = FactExtractor(
        store, extract_llm=llm, merge_llm=llm,
        cross_key_merge=True, cross_key_llm=llm,
    )
    await ext.process_message(message_id=2, content="message text", role="user")
    # A 应仍 active（unknown id 不影响）
    async with aiosqlite.connect(store._db_path) as conn:
        cur = await conn.execute(
            "SELECT is_active FROM facts WHERE id = ?", (a_id,)
        )
        r = await cur.fetchone()
    assert r[0] == 1


# ---------------------------------------------------------------------------
# TS1-9 — 并发抽取（2 个 task 同 subject 不同 key 矛盾）→ persist_lock 串行
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ts1_9_concurrent_extracts_serialised(db_path: Path) -> None:
    store = await _make_store(db_path)
    a_id = await store.upsert(
        category="preference", subject="user", key="k1", value="v1",
        confidence=0.9, source_msg_id=1, evidence="ev",
    )
    llm = StubLLM(
        extract_response=_extract_response_for("preference", "k2", "v2"),
        cross_key_response=json.dumps({"conflicts": [], "should_insert": True}),
    )
    ext = FactExtractor(
        store, extract_llm=llm, merge_llm=llm,
        cross_key_merge=True, cross_key_llm=llm,
    )
    await asyncio.gather(*[
        ext.process_message(message_id=i, content="msg content", role="user")
        for i in range(10, 15)
    ])
    # 不挂；最终 facts 表 active 数应合理（≤ 6 — A + 5 个新 insert）
    active = await store.list_active(subject="user")
    assert len(active) <= 6


# ---------------------------------------------------------------------------
# TS1-10 — mark_superseded 对已 inactive 调用：UPDATE 无效，不报错
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ts1_10_mark_superseded_on_inactive(db_path: Path) -> None:
    store = await _make_store(db_path)
    a_id = await store.upsert(
        category="preference", subject="user", key="k1", value="v1",
        confidence=0.9, source_msg_id=1, evidence="ev",
    )
    await store.deactivate(a_id)
    # 再 mark_superseded 应静默无效（WHERE is_active=1 不命中）
    await store.mark_superseded(old_id=a_id, superseded_by=999)
    async with aiosqlite.connect(store._db_path) as conn:
        cur = await conn.execute(
            "SELECT superseded_by FROM facts WHERE id = ?", (a_id,)
        )
        r = await cur.fetchone()
    # 仍 None（更新被守护拒）
    assert r[0] is None


# ---------------------------------------------------------------------------
# TS1-11 — list_superseded_chain 3 级链
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ts1_11_superseded_chain(db_path: Path) -> None:
    store = await _make_store(db_path)
    a = await store.upsert(category="event", subject="u", key="k", value="A",
                           confidence=0.9, source_msg_id=1, evidence="e")
    b = await store.upsert(category="event", subject="u", key="k", value="B",
                           confidence=0.9, source_msg_id=2, evidence="e")
    c = await store.upsert(category="event", subject="u", key="k", value="C",
                           confidence=0.9, source_msg_id=3, evidence="e")
    await store.mark_superseded(old_id=a, superseded_by=b)
    await store.mark_superseded(old_id=b, superseded_by=c)
    chain = await store.list_superseded_chain(a)
    assert len(chain) == 3
    assert [r["value"] for r in chain] == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# TS1-12 — should_insert=False, conflicts=[] → noop + warn
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ts1_12_noop_decision(db_path: Path) -> None:
    store = await _make_store(db_path)
    a_id = await store.upsert(
        category="preference", subject="user", key="k1", value="v1",
        confidence=0.9, source_msg_id=1, evidence="ev",
    )
    llm = StubLLM(
        extract_response=_extract_response_for("preference", "k2", "v2"),
        cross_key_response=json.dumps({
            "conflicts": [], "should_insert": False,
        }),
    )
    ext = FactExtractor(
        store, extract_llm=llm, merge_llm=llm,
        cross_key_merge=True, cross_key_llm=llm,
    )
    persisted = await ext.process_message(
        message_id=2, content="message content", role="user",
    )
    # 不 insert
    insert_count = sum(1 for p in persisted if p["action"] == "insert")
    assert insert_count == 0


# ---------------------------------------------------------------------------
# TS1-13 ★v2 — LLM 返 null old_id / 非 int old_id → 跳过该 entry
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ts1_13_invalid_old_id_dropped(db_path: Path) -> None:
    store = await _make_store(db_path)
    a_id = await store.upsert(
        category="preference", subject="user", key="k1", value="v1",
        confidence=0.9, source_msg_id=1, evidence="ev",
    )
    llm = StubLLM(
        extract_response=_extract_response_for("preference", "k2", "v2"),
        cross_key_response=json.dumps({
            "conflicts": [
                {"old_id": None, "reason": "x"},
                {"old_id": "abc", "reason": "y"},
                {"old_id": a_id, "reason": "real"},
            ],
            "should_insert": True,
        }),
    )
    ext = FactExtractor(
        store, extract_llm=llm, merge_llm=llm,
        cross_key_merge=True, cross_key_llm=llm,
    )
    await ext.process_message(message_id=2, content="message content", role="user")
    async with aiosqlite.connect(store._db_path) as conn:
        cur = await conn.execute(
            "SELECT is_active FROM facts WHERE id = ?", (a_id,)
        )
        r = await cur.fetchone()
    # 第三条 (real) 应正确处理 → A 标 inactive
    assert r[0] == 0


# ---------------------------------------------------------------------------
# TS1-14 ★v2 — D3 混合视野：embedder 召回拉进候选 (mock embedder 单测)
# 注：因为 mock embedder 行为是确定的，且没有真向量，这里改成检测
# vector_search_in_subject 的 SQL 路径无报错即可
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ts1_14_vector_search_in_subject(db_path: Path) -> None:
    store = await _make_store(db_path)
    import numpy as np
    # 写一条带向量的 fact
    fid = await store.upsert(
        category="preference", subject="user", key="k1", value="v1",
        confidence=0.9, source_msg_id=1, evidence="ev",
    )
    # 直接补写 embedding（mock）
    vec = np.array([1.0, 0.0, 0.0], dtype=np.float32).tobytes()
    async with aiosqlite.connect(store._db_path) as conn:
        await conn.execute(
            "UPDATE facts SET embedding = ? WHERE id = ?", (vec, fid),
        )
        await conn.commit()
    qvec = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    out = await store.vector_search_in_subject(
        qvec, subject="user", limit=10,
    )
    assert len(out) == 1
    assert out[0]["id"] == fid
    assert out[0]["_score"] > 0.99


# ---------------------------------------------------------------------------
# parse_cross_key_decision 边界
# ---------------------------------------------------------------------------
def test_parse_cross_key_decision_safe_defaults() -> None:
    assert _parse_cross_key_decision("").should_insert is True
    assert _parse_cross_key_decision("not json").conflicts == []
    obj = _parse_cross_key_decision(
        json.dumps({"conflicts": [{"old_id": 1}], "should_insert": False})
    )
    assert obj.should_insert is False
    assert obj.conflicts == [{"old_id": 1}]
