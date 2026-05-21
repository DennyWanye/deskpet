"""Phase B — tests for deskpet.memory.facts.

Coverage:
  * FactsStore CRUD: upsert / find_active / list_active / search /
    deactivate / update_value / touch_recalled / daily_decay
  * FactExtractor:
    - empty content → []
    - LLM returns empty array → []
    - new fact (no existing) → insert
    - existing fact + identical value → no_op (no LLM merge call)
    - existing fact + new value, LLM decides replace → old deactivated, new active
    - existing fact + new value, LLM decides merge → updated in place
    - LLM extract fails → graceful empty result, no raise
    - LLM merge fails → conservative no_op, no raise
  * Parsers (_parse_extracted / _parse_merge_decision) tolerate fenced
    / pre/post prose / malformed JSON.
  * Retriever integration: facts_store wired + facts_weight > 0 →
    fact rows surface in recall results; facts_weight=0 → byte-identical
    legacy result.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
import aiosqlite

from deskpet.memory.facts import (
    FactsStore,
    FactExtractor,
    ExtractedFact,
    MergeDecision,
    _parse_extracted,
    _parse_merge_decision,
    _CATEGORY_DECAY,
)
from deskpet.memory.migrator import ensure_v9


@pytest_asyncio.fixture
async def db_path(tmp_path: Path) -> Path:
    db = tmp_path / "state.db"
    await ensure_v9(db)
    return db


class _FakeLLM:
    """Async callable returning a queued list of responses."""

    def __init__(self, responses: list[str | Exception]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


# ----------------------------------------------------------------------
# FactsStore
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_facts_store_upsert_and_find(db_path: Path) -> None:
    store = FactsStore(db_path)
    fid = await store.upsert(
        category="preference",
        subject="user",
        key="favorite_drink",
        value="oolong tea",
        confidence=0.9,
        source_msg_id=10,
        evidence="user said 'I love oolong'",
    )
    assert fid > 0
    row = await store.find_active(subject="user", key="favorite_drink")
    assert row is not None
    assert row["value"] == "oolong tea"
    assert row["is_active"] == 1
    assert row["decay_rate"] == pytest.approx(_CATEGORY_DECAY["preference"])


@pytest.mark.asyncio
async def test_facts_store_deactivate_keeps_history(db_path: Path) -> None:
    store = FactsStore(db_path)
    fid = await store.upsert(
        category="preference", subject="user", key="drink",
        value="tea", confidence=0.8, source_msg_id=10, evidence="...",
    )
    await store.deactivate(fid)
    assert (await store.find_active(subject="user", key="drink")) is None
    # Row still exists, just is_active=0
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute(
            "SELECT is_active FROM facts WHERE id = ?", (fid,)
        )
        row = await cur.fetchone()
    assert row is not None and row[0] == 0


@pytest.mark.asyncio
async def test_facts_store_update_value(db_path: Path) -> None:
    store = FactsStore(db_path)
    fid = await store.upsert(
        category="preference", subject="user", key="drink",
        value="tea", confidence=0.5, source_msg_id=10, evidence="ev1",
    )
    await store.update_value(
        fid, value="oolong tea", confidence=0.9, evidence="ev2"
    )
    row = await store.find_active(subject="user", key="drink")
    assert row is not None
    assert row["value"] == "oolong tea"
    assert row["confidence"] == pytest.approx(0.9)
    assert row["evidence"] == "ev2"


@pytest.mark.asyncio
async def test_facts_store_search_like_match(db_path: Path) -> None:
    store = FactsStore(db_path)
    await store.upsert(
        category="preference", subject="user", key="drink", value="oolong tea",
        confidence=0.8, source_msg_id=10, evidence="liked oolong since 2020",
    )
    await store.upsert(
        category="profile", subject="user", key="city", value="Beijing",
        confidence=1.0, source_msg_id=11, evidence="lives in Beijing",
    )
    hits = await store.search("oolong", limit=10)
    assert len(hits) == 1
    assert hits[0]["value"] == "oolong tea"


@pytest.mark.asyncio
async def test_facts_store_list_active_filters(db_path: Path) -> None:
    store = FactsStore(db_path)
    await store.upsert(
        category="preference", subject="user", key="a", value="1",
        confidence=0.5, source_msg_id=None, evidence="",
    )
    await store.upsert(
        category="profile", subject="user", key="b", value="2",
        confidence=0.5, source_msg_id=None, evidence="",
    )
    await store.upsert(
        category="preference", subject="pet", key="c", value="3",
        confidence=0.5, source_msg_id=None, evidence="",
    )
    by_subject = await store.list_active(subject="user")
    assert {r["key"] for r in by_subject} == {"a", "b"}
    by_category = await store.list_active(category="preference")
    assert {r["key"] for r in by_category} == {"a", "c"}


@pytest.mark.asyncio
async def test_facts_store_touch_and_decay(db_path: Path) -> None:
    store = FactsStore(db_path)
    now0 = time.time()
    fid = await store.upsert(
        category="event", subject="user", key="last_login",
        value="2026-01-01", confidence=1.0, source_msg_id=None, evidence="",
    )
    # Force decay_last_touch into past so decay actually changes the value
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "UPDATE facts SET last_recalled = ?, updated_at = ? WHERE id = ?",
            (now0 - 86400 * 30, now0 - 86400 * 30, fid),
        )
        await conn.commit()
    # Event has decay 0.05 — 30 days → exp(-1.5) ≈ 0.223
    changed = await store.daily_decay(now=now0)
    assert changed == 1
    row = await store.find_active(subject="user", key="last_login")
    assert row is not None
    assert 0.15 < row["confidence"] < 0.30

    # Profile category has decay 0.0 — never changes regardless of age.
    # Use a fresh DB to assert this in isolation (don't get tangled with
    # the already-decayed event fact from above).


@pytest.mark.asyncio
async def test_facts_store_profile_never_decays(db_path: Path) -> None:
    store = FactsStore(db_path)
    now0 = time.time()
    pfid = await store.upsert(
        category="profile", subject="user", key="name",
        value="Alice", confidence=1.0, source_msg_id=None, evidence="",
    )
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "UPDATE facts SET last_recalled = ?, updated_at = ? WHERE id = ?",
            (now0 - 86400 * 365, now0 - 86400 * 365, pfid),
        )
        await conn.commit()
    n2 = await store.daily_decay(now=now0)
    assert n2 == 0  # profile.decay_rate=0 → factor=1.0 → no change


# ----------------------------------------------------------------------
# FactExtractor — orchestration
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extractor_empty_content_skipped(db_path: Path) -> None:
    extractor = FactExtractor(
        FactsStore(db_path),
        extract_llm=_FakeLLM(["[]"]),
    )
    out = await extractor.process_message(message_id=1, content="", role="user")
    assert out == []


@pytest.mark.asyncio
async def test_extractor_tool_role_skipped(db_path: Path) -> None:
    extractor = FactExtractor(
        FactsStore(db_path),
        extract_llm=_FakeLLM([]),  # never called
    )
    out = await extractor.process_message(
        message_id=1, content="long enough content", role="tool"
    )
    assert out == []


@pytest.mark.asyncio
async def test_extractor_no_facts_extracted(db_path: Path) -> None:
    extractor = FactExtractor(
        FactsStore(db_path),
        extract_llm=_FakeLLM(["[]"]),
    )
    out = await extractor.process_message(
        message_id=1,
        content="嗯，知道了，谢谢。",
        role="user",
    )
    assert out == []


@pytest.mark.asyncio
async def test_extractor_inserts_brand_new_fact(db_path: Path) -> None:
    payload = json.dumps([{
        "category": "preference",
        "subject": "user",
        "key": "favorite_drink",
        "value": "oolong tea",
        "confidence": 0.9,
        "evidence": "I love oolong",
    }])
    extractor = FactExtractor(
        FactsStore(db_path),
        extract_llm=_FakeLLM([payload]),
    )
    out = await extractor.process_message(
        message_id=42,
        content="I love oolong tea.",
        role="user",
    )
    assert len(out) == 1
    assert out[0]["action"] == "insert"
    row = await FactsStore(db_path).find_active(
        subject="user", key="favorite_drink"
    )
    assert row is not None
    assert row["source_msg_id"] == 42


@pytest.mark.asyncio
async def test_extractor_identical_value_noop_without_merge_llm_call(
    db_path: Path,
) -> None:
    # Seed one active fact
    store = FactsStore(db_path)
    await store.upsert(
        category="preference", subject="user", key="favorite_drink",
        value="oolong tea", confidence=0.9, source_msg_id=10, evidence="...",
    )
    # Now extractor returns the SAME value — should short-circuit to no_op
    extract_resp = json.dumps([{
        "category": "preference", "subject": "user", "key": "favorite_drink",
        "value": "oolong tea", "confidence": 0.7, "evidence": "again",
    }])
    merge_llm = _FakeLLM([])  # MUST NOT be called
    extractor = FactExtractor(
        store,
        extract_llm=_FakeLLM([extract_resp]),
        merge_llm=merge_llm,
    )
    out = await extractor.process_message(
        message_id=99, content="I still love oolong", role="user",
    )
    assert out == []  # no_op → not in persisted list
    assert merge_llm.prompts == []  # short-circuit worked


@pytest.mark.asyncio
async def test_extractor_replace_path(db_path: Path) -> None:
    store = FactsStore(db_path)
    fid_old = await store.upsert(
        category="preference", subject="user", key="drink",
        value="coffee", confidence=0.8, source_msg_id=10, evidence="loves coffee",
    )
    extract_resp = json.dumps([{
        "category": "preference", "subject": "user", "key": "drink",
        "value": "tea", "confidence": 0.9, "evidence": "switched to tea",
    }])
    merge_resp = json.dumps({"action": "replace", "value": "tea",
                              "reason": "user explicitly switched"})
    extractor = FactExtractor(
        store,
        extract_llm=_FakeLLM([extract_resp]),
        merge_llm=_FakeLLM([merge_resp]),
    )
    out = await extractor.process_message(
        message_id=20, content="I switched to tea", role="user",
    )
    assert len(out) == 1
    assert out[0]["action"] == "replace"
    # Old fact deactivated
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute(
            "SELECT is_active FROM facts WHERE id = ?", (fid_old,)
        )
        row = await cur.fetchone()
    assert row[0] == 0
    # New active fact has the new value
    new_row = await store.find_active(subject="user", key="drink")
    assert new_row["value"] == "tea"


@pytest.mark.asyncio
async def test_extractor_merge_path(db_path: Path) -> None:
    store = FactsStore(db_path)
    fid = await store.upsert(
        category="preference", subject="user", key="drink",
        value="tea", confidence=0.6, source_msg_id=10, evidence="likes tea",
    )
    extract_resp = json.dumps([{
        "category": "preference", "subject": "user", "key": "drink",
        "value": "oolong tea", "confidence": 0.85,
        "evidence": "specifically oolong",
    }])
    merge_resp = json.dumps({
        "action": "merge", "value": "oolong tea",
        "reason": "refines the kind of tea",
    })
    extractor = FactExtractor(
        store,
        extract_llm=_FakeLLM([extract_resp]),
        merge_llm=_FakeLLM([merge_resp]),
    )
    out = await extractor.process_message(
        message_id=21, content="actually oolong", role="user",
    )
    assert len(out) == 1
    assert out[0]["action"] == "merge"
    # Same fact id, value updated to "oolong tea", confidence bumped
    row = await store.find_active(subject="user", key="drink")
    assert row is not None
    assert row["id"] == fid
    assert row["value"] == "oolong tea"
    assert row["confidence"] == pytest.approx(0.85)


@pytest.mark.asyncio
async def test_extractor_llm_failure_returns_empty(db_path: Path) -> None:
    extractor = FactExtractor(
        FactsStore(db_path),
        extract_llm=_FakeLLM([RuntimeError("LLM down")]),
    )
    out = await extractor.process_message(
        message_id=1, content="Some long enough content here.",
        role="user",
    )
    assert out == []


@pytest.mark.asyncio
async def test_extractor_merge_llm_failure_is_noop(db_path: Path) -> None:
    store = FactsStore(db_path)
    await store.upsert(
        category="preference", subject="user", key="drink",
        value="coffee", confidence=0.8, source_msg_id=10, evidence="...",
    )
    extract_resp = json.dumps([{
        "category": "preference", "subject": "user", "key": "drink",
        "value": "tea", "confidence": 0.9, "evidence": "...",
    }])
    extractor = FactExtractor(
        store,
        extract_llm=_FakeLLM([extract_resp]),
        merge_llm=_FakeLLM([RuntimeError("merge llm 503")]),
    )
    out = await extractor.process_message(
        message_id=20, content="switched", role="user",
    )
    assert out == []  # merge failed → no_op default → no persisted change
    row = await store.find_active(subject="user", key="drink")
    assert row["value"] == "coffee"  # unchanged


@pytest.mark.asyncio
async def test_extractor_invalid_extracted_fact_dropped(db_path: Path) -> None:
    # Missing 'key' → invalid → silently dropped
    payload = json.dumps([{
        "category": "preference", "subject": "user", "key": "",
        "value": "tea", "confidence": 0.9, "evidence": "x",
    }])
    extractor = FactExtractor(
        FactsStore(db_path),
        extract_llm=_FakeLLM([payload]),
    )
    out = await extractor.process_message(
        message_id=1, content="aaaaaaaa long enough", role="user",
    )
    assert out == []


# ----------------------------------------------------------------------
# Parsers
# ----------------------------------------------------------------------


def test_parse_extracted_plain_json() -> None:
    raw = '[{"category":"preference","subject":"user","key":"k","value":"v","confidence":0.5,"evidence":"e"}]'
    parsed = _parse_extracted(raw)
    assert len(parsed) == 1
    f = parsed[0]
    assert f.category == "preference"
    assert f.key == "k"


def test_parse_extracted_fenced_with_prose() -> None:
    raw = (
        "Here you go:\n```json\n"
        '[{"category":"profile","subject":"user","key":"name","value":"Alice","confidence":0.95,"evidence":"I am Alice"}]\n'
        "```\nDone."
    )
    parsed = _parse_extracted(raw)
    assert len(parsed) == 1
    assert parsed[0].category == "profile"


def test_parse_extracted_garbage_empty() -> None:
    assert _parse_extracted("not json") == []
    assert _parse_extracted("") == []
    assert _parse_extracted('[{"missing":"keys"}]')[0].is_valid() is False


def test_parse_merge_decision_happy() -> None:
    dec = _parse_merge_decision('{"action":"replace","value":"tea","reason":"x"}')
    assert dec.action == "replace"
    assert dec.value == "tea"


def test_parse_merge_decision_unknown_action_falls_back() -> None:
    dec = _parse_merge_decision('{"action":"clobber","value":"x"}')
    assert dec.action == "no_op"


def test_parse_merge_decision_invalid_falls_back() -> None:
    dec = _parse_merge_decision("not json")
    assert dec.action == "no_op"
    dec = _parse_merge_decision("")
    assert dec.action == "no_op"


def test_extracted_fact_normalize_and_validate() -> None:
    f = ExtractedFact(
        category="  Preference  ",
        subject="  User  ",
        key=" Favorite Drink ",
        value=" tea  ",
        confidence=1.5,  # over-bound
        evidence="ev" * 200,
    )
    n = f.normalize()
    assert n.category == "preference"
    assert n.subject == "user"
    assert n.key == "favorite_drink"
    assert n.value == "tea"
    assert n.confidence == 1.0  # clamped
    assert len(n.evidence) <= 200
    assert n.is_valid()


# ----------------------------------------------------------------------
# EnhancedRetriever integration — Phase B
# The base Retriever stays unmodified; EnhancedRetriever wraps it.
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enhanced_retriever_facts_weight_zero_byte_identical(
    db_path: Path,
) -> None:
    """facts_weight=0 must produce the same result as the base Retriever.

    Even with a populated FactsStore wired in, weight=0 + no reranker =
    pass-through. This is the Strangler-Fig guarantee: feature flag OFF
    means zero behaviour change.
    """
    from deskpet.memory.session_db import SessionDB
    from deskpet.memory.embedder import Embedder
    from deskpet.memory.retriever import Retriever
    from deskpet.memory.enhanced_retriever import EnhancedRetriever

    sdb = SessionDB(db_path)
    await sdb.initialize()
    await sdb.create_session("s1")
    for i in range(5):
        await sdb.append_message("s1", "user", f"message about subject {i}")

    embedder = Embedder(model_path=None, use_mock_when_missing=True)
    base = Retriever(sdb, embedder)
    legacy = await base.recall("subject", top_k=5)

    facts = FactsStore(db_path)
    await facts.upsert(
        category="preference", subject="user", key="drink",
        value="subject something", confidence=0.9,
        source_msg_id=None, evidence="...",
    )
    enhanced = EnhancedRetriever(
        Retriever(sdb, embedder), facts_store=facts, facts_weight=0.0
    )
    wrapped = await enhanced.recall("subject", top_k=5)

    assert [h.message_id for h in legacy] == [h.message_id for h in wrapped]


@pytest.mark.asyncio
async def test_enhanced_retriever_facts_surface_when_weight_positive(
    db_path: Path,
) -> None:
    """facts_weight > 0 + matching fact → fact synthetic id appears in result."""
    from deskpet.memory.session_db import SessionDB
    from deskpet.memory.embedder import Embedder
    from deskpet.memory.retriever import Retriever
    from deskpet.memory.enhanced_retriever import (
        EnhancedRetriever, _FACT_ID_OFFSET,
    )

    sdb = SessionDB(db_path)
    await sdb.initialize()
    await sdb.create_session("s1")
    await sdb.append_message("s1", "user", "some random unrelated text")

    embedder = Embedder(model_path=None, use_mock_when_missing=True)
    facts = FactsStore(db_path)
    fid = await facts.upsert(
        category="preference", subject="user", key="favorite_drink",
        value="oolong tea", confidence=0.9, source_msg_id=None,
        evidence="loves oolong",
    )

    r = EnhancedRetriever(
        Retriever(sdb, embedder), facts_store=facts, facts_weight=1.0
    )
    hits = await r.recall("oolong", top_k=5)
    ids = [h.message_id for h in hits]
    assert (_FACT_ID_OFFSET + fid) in ids


@pytest.mark.asyncio
async def test_enhanced_retriever_facts_content_renders_for_llm(
    db_path: Path,
) -> None:
    """A fact hit's text should be a human-readable `[fact] key: value` line."""
    from deskpet.memory.session_db import SessionDB
    from deskpet.memory.embedder import Embedder
    from deskpet.memory.retriever import Retriever
    from deskpet.memory.enhanced_retriever import EnhancedRetriever

    sdb = SessionDB(db_path)
    await sdb.initialize()
    await sdb.create_session("s1")
    await sdb.append_message("s1", "user", "noise")

    embedder = Embedder(model_path=None, use_mock_when_missing=True)
    facts = FactsStore(db_path)
    await facts.upsert(
        category="preference", subject="user", key="favorite_drink",
        value="oolong tea", confidence=0.95, source_msg_id=None,
        evidence="user said they love oolong",
    )

    r = EnhancedRetriever(
        Retriever(sdb, embedder), facts_store=facts, facts_weight=1.0
    )
    hits = await r.recall("oolong", top_k=5)
    fact_hits = [h for h in hits if h.text.startswith("[fact]")]
    assert len(fact_hits) == 1
    assert "favorite_drink" in fact_hits[0].text
    assert "oolong tea" in fact_hits[0].text
