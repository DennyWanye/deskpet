# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-M0.1 — memory-v2 死代码体检 + smoke 回归。

逐个构造 Phase A-E 模块并做最小真实调用，捕获接口腐烂
（TypeError / AttributeError）。此前 memory-v2 单元测试全绿却整批未接入
生产 —— 本文件固化为长期回归，确保升级激活后这些模块不再悄悄腐烂。

不测"召回质量"或"接入后行为"（那是各 WI 的集成测试）；只确认每个 v2
模块对**当前** SessionDB/config/Embedder 接口仍可构造、可跑最小调用。
"""
from __future__ import annotations

import pytest
import aiosqlite

from deskpet.memory.memory_v2_schema import (
    ensure_memory_v2_tables,
    _reset_cache_for_tests,
)
from deskpet.memory.facts import FactsStore, FactExtractor
from deskpet.memory.workspace import WorkspaceMemoryStore
from deskpet.memory.chunker import MessageChunker
from deskpet.memory.reflection import (
    ReflectionWorker,
    SkillMemoryStore,
    SkillMemoryEntry,
)
from deskpet.memory.query_rewriter import NoopQueryRewriter, LLMQueryRewriter
from deskpet.memory.reranker import MockReranker
from deskpet.memory.enhanced_retriever import EnhancedRetriever


@pytest.fixture
def db_path(tmp_path):
    _reset_cache_for_tests()
    return tmp_path / "state.db"


async def _mock_llm(_prompt: str) -> str:
    """Trivial LLMCall stub — returns an empty JSON array (no facts)."""
    return "[]"


# ---------------------------------------------------------------------------
# T0-2 — 7 张 v2 表能建出
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ensure_v2_tables_creates_all_seven(db_path):
    await ensure_memory_v2_tables(db_path)
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        names = {r[0] for r in await cur.fetchall()}
    for t in (
        "facts", "messages_chunks", "workspace_state", "skill_memory",
        "memory_qa_set", "memory_eval_run", "memory_user_feedback",
    ):
        assert t in names, f"v2 table {t!r} not created — schema rot"


# ---------------------------------------------------------------------------
# T0-1 — 逐模块构造 + 最小真实调用
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_facts_store_smoke(db_path):
    store = FactsStore(db_path)
    fid = await store.upsert(
        category="preference", subject="user", key="食物偏好",
        value="对花生过敏", confidence=0.9, source_msg_id=1,
        evidence="我对花生过敏",
    )
    assert isinstance(fid, int) and fid > 0
    active = await store.list_active(subject="user")
    assert any(f["key"] == "食物偏好" for f in active)
    hits = await store.search("花生", limit=5)
    assert isinstance(hits, list)


@pytest.mark.asyncio
async def test_fact_extractor_smoke(db_path):
    store = FactsStore(db_path)
    extractor = FactExtractor(store, extract_llm=_mock_llm, merge_llm=_mock_llm)
    # mock LLM returns "[]" → no facts; just assert it doesn't raise.
    await extractor.process_message(message_id=1, content="我对花生过敏", role="user")


@pytest.mark.asyncio
async def test_workspace_store_smoke(db_path):
    store = WorkspaceMemoryStore(db_path)
    await store.record_action(
        session_id="s1", path="a.py", action="write", content="print(1)",
    )
    row = await store.get(session_id="s1", path="a.py")
    assert row is not None and row["path"] == "a.py"


@pytest.mark.asyncio
async def test_chunker_smoke(db_path):
    chunker = MessageChunker(db_path)
    ids = await chunker.chunk_message(message_id=1, content="一段消息内容" * 30)
    assert isinstance(ids, list) and len(ids) >= 1


@pytest.mark.asyncio
async def test_skill_memory_store_smoke(db_path):
    store = SkillMemoryStore(db_path)
    sid = await store.add(SkillMemoryEntry(
        name="restart-backend", description="重启后端",
        trigger_pattern="后端.*崩", steps=["taskkill", "重启"],
    ))
    assert isinstance(sid, int) and sid > 0
    assert any(s["name"] == "restart-backend" for s in await store.list_all())


@pytest.mark.asyncio
async def test_reflection_worker_smoke(db_path):
    store = FactsStore(db_path)
    worker = ReflectionWorker(db_path, store, _mock_llm)
    # run_once with an empty DB → no recent turns → returns None, no raise.
    result = await worker.run_once()
    assert result is None or isinstance(result, int)


@pytest.mark.asyncio
async def test_query_rewriter_smoke():
    assert await NoopQueryRewriter().rewrite("查询") == "查询"
    rewritten = await LLMQueryRewriter(_mock_llm).rewrite("零食")
    assert isinstance(rewritten, str)


@pytest.mark.asyncio
async def test_reranker_smoke():
    rr = MockReranker()
    assert rr.is_mock() is True
    out = await rr.rerank("query", [{"text": "a"}, {"text": "b"}])
    assert isinstance(out, list)


def test_enhanced_retriever_constructs():
    """EnhancedRetriever 构造签名未腐烂；facts_weight 默认 0.0（评审 D5）。"""
    class _StubBase:
        policy = None

    er = EnhancedRetriever(_StubBase())  # type: ignore[arg-type]
    assert er._facts_weight == 0.0  # 默认 0 = facts 不进结果，接入时必须显式传
    er2 = EnhancedRetriever(_StubBase(), facts_weight=0.2)  # type: ignore[arg-type]
    assert er2._facts_weight == 0.2
