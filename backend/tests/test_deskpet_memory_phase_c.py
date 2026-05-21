"""Phase C — tests for reranker / chunker / query rewriter.

Coverage:
  * MessageChunker
      - short text → single chunk
      - long text → multiple chunks at sentence boundaries
      - mega-sentence over hard cap → hard split
      - empty / whitespace → []
      - idempotent re-chunk drops old rows
      - chunks_for_message returns ordered rows
  * MockReranker / BGEReranker(mock_mode)
      - empty candidates → []
      - exact substring boosts to top
      - deterministic ordering (same input → same output)
  * NoopQueryRewriter / LLMQueryRewriter
      - empty → original
      - LLM timeout → original
      - LLM refusal phrase → original
      - LLM strips wrapping quotes
  * Retriever integration:
      - no reranker → identical to legacy
      - reranker plugged in → top item reordered when reranker says so
      - query_rewriter only triggered for short queries
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
import pytest_asyncio
import aiosqlite

from deskpet.memory.chunker import (
    MessageChunker,
    split_into_chunks,
    _MIN_CHUNK_THRESHOLD,
    _MAX_CHUNK_CHARS,
)
from deskpet.memory.reranker import MockReranker, BGEReranker
from deskpet.memory.query_rewriter import (
    NoopQueryRewriter, LLMQueryRewriter,
)
from deskpet.memory.migrator import ensure_v9


@pytest_asyncio.fixture
async def db_path(tmp_path: Path) -> Path:
    db = tmp_path / "state.db"
    await ensure_v9(db)
    return db


# ----------------------------------------------------------------------
# Chunker — pure split function
# ----------------------------------------------------------------------


def test_split_empty_returns_empty() -> None:
    assert split_into_chunks("") == []
    assert split_into_chunks("   \n   ") == []


def test_split_short_returns_single_chunk() -> None:
    text = "短消息内容。"
    out = split_into_chunks(text)
    assert out == [text]


def test_split_long_text_into_multiple_chunks() -> None:
    # Build a string longer than _MIN_CHUNK_THRESHOLD with distinct sentences
    # separated by ASCII space so _SENTENCE_SPLIT_RE actually fires
    # (lookbehind requires whitespace after `。`/`.`/etc).
    sentences = [f"this is sentence number {i}." for i in range(40)]
    text = " ".join(sentences)
    assert len(text) > _MIN_CHUNK_THRESHOLD
    out = split_into_chunks(text)
    assert len(out) >= 2
    # Each non-last chunk should end with a sentence terminator
    for c in out[:-1]:
        assert c[-1] in "。！？.!?"


def test_split_mega_sentence_hard_capped() -> None:
    # Single sentence with no boundaries, way over MAX
    mega = "x" * (_MAX_CHUNK_CHARS * 3) + "。"
    out = split_into_chunks(mega)
    # Expect at least 3 chunks since len > 3 × MAX
    assert len(out) >= 3
    for c in out:
        assert len(c) <= _MAX_CHUNK_CHARS


def test_split_mixed_chinese_english() -> None:
    # Use double-newline groups to guarantee boundary detection regardless
    # of language-specific punctuation handling.
    blocks = [
        "Hello world. This is the first sentence.",
        "这是中文句子。再来一段中文。",
    ] * 20  # 40 blocks alternating, plenty of content
    text = "\n\n".join(blocks)
    assert len(text) > _MIN_CHUNK_THRESHOLD
    out = split_into_chunks(text)
    assert len(out) >= 2
    joined = " ".join(out)
    assert "Hello" in joined and "中文" in joined


# ----------------------------------------------------------------------
# Chunker — persistence
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chunker_persist_single_short(db_path: Path) -> None:
    chunker = MessageChunker(db_path)
    ids = await chunker.chunk_message(message_id=1, content="短消息。")
    assert len(ids) == 1
    rows = await chunker.chunks_for_message(1)
    assert len(rows) == 1
    assert rows[0]["chunk_index"] == 0
    assert rows[0]["text"] == "短消息。"


@pytest.mark.asyncio
async def test_chunker_persist_long_multiple(db_path: Path) -> None:
    chunker = MessageChunker(db_path)
    long_text = "句子%d。" % 0
    long_text += "".join("更多内容%d。" % i for i in range(80))
    ids = await chunker.chunk_message(message_id=2, content=long_text)
    assert len(ids) >= 2
    rows = await chunker.chunks_for_message(2)
    indices = [r["chunk_index"] for r in rows]
    assert indices == list(range(len(rows)))


@pytest.mark.asyncio
async def test_chunker_idempotent_re_chunk(db_path: Path) -> None:
    chunker = MessageChunker(db_path)
    await chunker.chunk_message(message_id=3, content="第一版内容。")
    await chunker.chunk_message(message_id=3, content="覆盖之后的内容。")
    rows = await chunker.chunks_for_message(3)
    assert len(rows) == 1
    assert rows[0]["text"] == "覆盖之后的内容。"


@pytest.mark.asyncio
async def test_chunker_total(db_path: Path) -> None:
    chunker = MessageChunker(db_path)
    await chunker.chunk_message(message_id=1, content="a")
    await chunker.chunk_message(message_id=2, content="b")
    assert await chunker.total_chunks() == 2


# ----------------------------------------------------------------------
# Reranker
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_reranker_empty_returns_empty() -> None:
    r = MockReranker()
    assert await r.rerank("any", []) == []


@pytest.mark.asyncio
async def test_mock_reranker_exact_substring_boosts() -> None:
    r = MockReranker()
    cands = [
        {"message_id": 1, "text": "totally unrelated content"},
        {"message_id": 2, "text": "the user loves oolong tea"},
        {"message_id": 3, "text": "another generic message"},
    ]
    out = await r.rerank("oolong", cands)
    # Id 2 contains "oolong" verbatim → boosted to top
    assert out[0][0] == 2
    # Each item's score is a float
    for mid, score in out:
        assert isinstance(score, float)


@pytest.mark.asyncio
async def test_mock_reranker_deterministic() -> None:
    r = MockReranker()
    cands = [
        {"message_id": 10, "text": "alpha beta gamma"},
        {"message_id": 11, "text": "delta epsilon zeta"},
        {"message_id": 12, "text": "eta theta iota"},
    ]
    o1 = await r.rerank("query", cands)
    o2 = await r.rerank("query", cands)
    assert o1 == o2


@pytest.mark.asyncio
async def test_bge_reranker_falls_back_to_mock_when_weights_missing(
    tmp_path: Path,
) -> None:
    # Point at a path that doesn't exist; use_mock_when_missing=True
    r = BGEReranker(model_path=tmp_path / "nonexistent")
    cands = [
        {"message_id": 1, "text": "hello world"},
        {"message_id": 2, "text": "I love oolong tea"},
    ]
    out = await r.rerank("oolong", cands)
    assert len(out) == 2
    assert r.is_mock()


@pytest.mark.asyncio
async def test_bge_reranker_top_k_caps_output() -> None:
    r = BGEReranker(model_path=None)
    cands = [
        {"message_id": i, "text": f"text {i}"} for i in range(10)
    ]
    out = await r.rerank("text", cands, top_k=3)
    assert len(out) == 3


# ----------------------------------------------------------------------
# Query rewriter
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_noop_rewriter_unchanged() -> None:
    nr = NoopQueryRewriter()
    assert await nr.rewrite("anything") == "anything"
    assert await nr.rewrite("") == ""


@pytest.mark.asyncio
async def test_llm_rewriter_empty_query_unchanged() -> None:
    async def llm(p): return "should not be called"
    r = LLMQueryRewriter(llm)
    assert await r.rewrite("") == ""


@pytest.mark.asyncio
async def test_llm_rewriter_strips_wrapping_quotes() -> None:
    async def llm(p): return '"expanded query"'
    r = LLMQueryRewriter(llm)
    out = await r.rewrite("short", context="recent chat")
    assert out == "expanded query"


@pytest.mark.asyncio
async def test_llm_rewriter_returns_original_on_refusal() -> None:
    async def llm(p): return "I cannot help with that."
    r = LLMQueryRewriter(llm)
    assert await r.rewrite("query") == "query"


@pytest.mark.asyncio
async def test_llm_rewriter_returns_original_on_timeout() -> None:
    async def slow_llm(p):
        await asyncio.sleep(0.5)
        return "would have been rewritten"
    r = LLMQueryRewriter(slow_llm, timeout_s=0.05)
    out = await r.rewrite("q")
    assert out == "q"


@pytest.mark.asyncio
async def test_llm_rewriter_returns_original_on_exception() -> None:
    async def bad_llm(p): raise RuntimeError("503")
    r = LLMQueryRewriter(bad_llm)
    assert await r.rewrite("q") == "q"


@pytest.mark.asyncio
async def test_llm_rewriter_returns_original_on_empty_output() -> None:
    async def empty_llm(p): return "   "
    r = LLMQueryRewriter(empty_llm)
    assert await r.rewrite("q") == "q"


# ----------------------------------------------------------------------
# EnhancedRetriever integration (wrapper, not Retriever modification)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enhanced_retriever_no_plugins_is_byte_identical(db_path: Path) -> None:
    """With all plug-ins None, the wrapper is a pure pass-through."""
    from deskpet.memory.session_db import SessionDB
    from deskpet.memory.embedder import Embedder
    from deskpet.memory.retriever import Retriever
    from deskpet.memory.enhanced_retriever import EnhancedRetriever

    sdb = SessionDB(db_path)
    await sdb.initialize()
    await sdb.create_session("s1")
    for i in range(5):
        await sdb.append_message("s1", "user", f"the user mentions topic {i}")

    embedder = Embedder(model_path=None, use_mock_when_missing=True)
    legacy_out = await Retriever(sdb, embedder).recall("topic", top_k=5)
    wrapped_out = await EnhancedRetriever(
        Retriever(sdb, embedder)
    ).recall("topic", top_k=5)
    assert [h.message_id for h in legacy_out] == [h.message_id for h in wrapped_out]


@pytest.mark.asyncio
async def test_enhanced_retriever_with_reranker_can_reorder(db_path: Path) -> None:
    """Reranker that boosts exact-substring match pulls target to #1."""
    from deskpet.memory.session_db import SessionDB
    from deskpet.memory.embedder import Embedder
    from deskpet.memory.retriever import Retriever
    from deskpet.memory.enhanced_retriever import EnhancedRetriever

    sdb = SessionDB(db_path)
    await sdb.initialize()
    await sdb.create_session("s1")
    ids: list[int] = []
    seed_texts = [
        "this is just noise",
        "more noise here",
        "yet another irrelevant line",
        "the user explicitly mentioned MAGIC_TOKEN here",  # target
        "trailing filler",
    ]
    for t in seed_texts:
        msg_id = await sdb.append_message("s1", "user", t)
        ids.append(msg_id)
    target_id = ids[3]

    embedder = Embedder(model_path=None, use_mock_when_missing=True)
    r = EnhancedRetriever(
        Retriever(sdb, embedder), reranker=MockReranker()
    )
    hits = await r.recall("MAGIC_TOKEN", top_k=5)
    returned_ids = [h.message_id for h in hits]
    assert target_id in returned_ids
    assert returned_ids[0] == target_id  # reranker pushed it to #1


@pytest.mark.asyncio
async def test_enhanced_retriever_query_rewriter_only_for_short_queries(
    db_path: Path,
) -> None:
    """Long queries should NOT invoke the rewriter (cost / distortion risk)."""
    from deskpet.memory.session_db import SessionDB
    from deskpet.memory.embedder import Embedder
    from deskpet.memory.retriever import Retriever
    from deskpet.memory.enhanced_retriever import EnhancedRetriever

    sdb = SessionDB(db_path)
    await sdb.initialize()
    await sdb.create_session("s1")
    await sdb.append_message("s1", "user", "some content")

    embedder = Embedder(model_path=None, use_mock_when_missing=True)

    class CountingRewriter:
        def __init__(self) -> None:
            self.calls = 0

        async def rewrite(self, q: str, **kw) -> str:
            self.calls += 1
            return "REWRITTEN"

    rewriter = CountingRewriter()
    r = EnhancedRetriever(
        Retriever(sdb, embedder), query_rewriter=rewriter
    )
    await r.recall("a" * 100, top_k=3)
    assert rewriter.calls == 0
    await r.recall("短查询?", top_k=3)
    assert rewriter.calls == 1
