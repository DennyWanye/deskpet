"""Phase A — tests for deskpet.memory.eval.

Coverage:
  * QASetBuilder
      - happy path: 3 sources, 2 questions each → 6 items inserted
      - LLM returns garbage → that source skipped, build still succeeds
      - empty messages table → returns []
      - parse_questions tolerates fenced JSON / single-string / bullets
      - clear() wipes per-source
  * MetricsRunner
      - target at rank 1 → hit@1=1, mrr=1
      - target at rank 5 → hit@5=1, hit@1=0, mrr=0.2
      - target missing → hit@*=0, mrr=0
      - empty QA set → returns zero report
  * FeedbackStore
      - record up/down, summary aggregates
      - record value=0 raises ValueError
      - top_negative_messages sorts correctly
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
import aiosqlite

from deskpet.memory.eval.qaset import QASetBuilder, _parse_questions
from deskpet.memory.eval.metrics import MetricsRunner, _extract_message_id
from deskpet.memory.eval.feedback import FeedbackStore
from deskpet.memory.migrator import ensure_v9


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_path(tmp_path: Path) -> Path:
    """Fresh state.db with migrations applied + sample messages seeded."""
    db = tmp_path / "state.db"
    await ensure_v9(db)
    async with aiosqlite.connect(db) as conn:
        # Seed sessions + messages with a deterministic id schema:
        #   id=10..14: user/assistant pairs with long-ish content (eligible)
        #   id=15:    a tool-call message (ineligible — tool_calls non-null)
        #   id=16:    a short "ok" (ineligible — too short)
        await conn.execute(
            "INSERT INTO sessions(id, created_at) VALUES ('s1', 1700000000.0)"
        )
        seed_msgs = [
            (10, "s1", "user",      "我对花生过敏，下次记得别给我推荐花生制品。", None, None),
            (11, "s1", "assistant", "好的，我会记住你对花生过敏。需要我也避开杏仁吗？", None, None),
            (12, "s1", "user",      "我喜欢喝乌龙茶，每天大约三杯。", None, None),
            (13, "s1", "assistant", "了解。乌龙茶咖啡因约 30mg/杯，三杯还在健康范围。", None, None),
            (14, "s1", "user",      "我老家是福建漳州，那里产很好的水仙乌龙。", None, None),
            (15, "s1", "assistant", "调用 list_directory 完成", '[{"name":"list_directory"}]', None),
            (16, "s1", "user",      "ok", None, None),
        ]
        for mid, sid, role, content, tcs, ri in seed_msgs:
            await conn.execute(
                "INSERT INTO messages(id, session_id, role, content, created_at, tool_calls) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (mid, sid, role, content, 1700000000.0 + mid, tcs),
            )
        await conn.commit()
    return db


class FakeLLM:
    """Async callable mimicking the LLMCall protocol.

    ``responses`` is a list of strings; each call pops the next one.
    When exhausted, raises StopIteration via plain IndexError so the
    builder can demonstrate per-source failure isolation.
    """

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self._responses.pop(0)


# ----------------------------------------------------------------------
# QASetBuilder
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qaset_build_happy_path(db_path: Path) -> None:
    llm = FakeLLM([
        '["你对哪种坚果过敏?", "我有什么饮食限制?"]',
        '["你喜欢什么茶?", "你每天喝几杯茶?"]',
        '["你老家在哪?", "漳州有什么特产?"]',
        '["问题1?", "问题2?"]',
        '["问题3?", "问题4?"]',
    ])
    builder = QASetBuilder(db_path, llm, rng_seed=42)
    items = await builder.build(
        max_items=6, include_archive=False, min_content_len=10,
    )
    # 5 eligible sources × 2 questions/source = 10 candidates, capped at 6.
    assert len(items) == 6
    qids = [it.expected_msg_id for it in items]
    # Builder must only pick from eligible messages (10..14).
    assert all(mid in {10, 11, 12, 13, 14} for mid in qids)
    # Each row in DB has the expected shape.
    rows = await builder.list_all()
    assert len(rows) == 6
    assert rows[0]["source"] == "llm_auto"
    assert "user_utterance" in rows[0]["tags"] or "assistant_utterance" in rows[0]["tags"]


@pytest.mark.asyncio
async def test_qaset_build_llm_failure_skips_source(db_path: Path) -> None:
    # 2 sources: first returns garbage that can't parse, second returns
    # valid JSON. Builder should skip the first, succeed for the second.
    llm = FakeLLM([
        "not json at all, sorry",          # first source → 0 questions
        '["你喜欢什么饮料?", "什么时候开始喝的?"]',  # second source → 2 questions
        '["问题x?", "问题y?"]',
        '["问题a?", "问题b?"]',
    ])
    builder = QASetBuilder(db_path, llm, rng_seed=123)
    items = await builder.build(
        max_items=4, include_archive=False, min_content_len=10,
    )
    # First source contributed 0 → at most 3 of 4 quotas filled (2+2 from
    # the others). Total non-zero.
    assert 1 <= len(items) <= 6
    # No items from the "garbage" LLM response.
    assert all(it.query for it in items)


@pytest.mark.asyncio
async def test_qaset_build_empty_db(tmp_path: Path) -> None:
    db = tmp_path / "empty.db"
    await ensure_v9(db)
    builder = QASetBuilder(db, FakeLLM([]))
    items = await builder.build(max_items=10)
    assert items == []


@pytest.mark.asyncio
async def test_qaset_add_manual_and_list(db_path: Path) -> None:
    builder = QASetBuilder(db_path, FakeLLM([]))
    qid = await builder.add_manual(
        "测试问题?", expected_msg_id=10, tags=["preference"], notes="hand"
    )
    assert qid > 0
    rows = await builder.list_all(source="manual")
    assert len(rows) == 1
    assert rows[0]["tags"] == ["preference"]
    assert rows[0]["notes"] == "hand"


@pytest.mark.asyncio
async def test_qaset_clear(db_path: Path) -> None:
    builder = QASetBuilder(db_path, FakeLLM([]))
    await builder.add_manual("q1?", 10)
    await builder.add_manual("q2?", 11)
    deleted = await builder.clear()
    assert deleted == 2
    assert await builder.list_all() == []


def test_parse_questions_plain_json() -> None:
    assert _parse_questions('["a?", "b?"]') == ["a?", "b?"]


def test_parse_questions_fenced_json() -> None:
    raw = '```json\n["a?", "b?"]\n```'
    assert _parse_questions(raw) == ["a?", "b?"]


def test_parse_questions_with_prose() -> None:
    raw = 'Sure! Here are some questions:\n["a?", "b?"]\nLet me know if more.'
    assert _parse_questions(raw) == ["a?", "b?"]


def test_parse_questions_bullet_fallback() -> None:
    raw = "1. 你喜欢什么饮料?\n2. 你住哪里?"
    parsed = _parse_questions(raw)
    assert parsed
    assert any("?" in q for q in parsed)


def test_parse_questions_garbage_returns_empty() -> None:
    assert _parse_questions("blah blah blah") == []
    assert _parse_questions("") == []


# ----------------------------------------------------------------------
# MetricsRunner
# ----------------------------------------------------------------------


@dataclass
class _FakeHit:
    message_id: int
    score: float = 0.0
    source: str = "vec"


class FakeRetriever:
    """Returns a configurable ordered list per query."""

    def __init__(self, mapping: dict[str, list[int]]) -> None:
        self._mapping = mapping

    async def recall(
        self, query: str, top_k: int | None = None, **kwargs
    ) -> list[_FakeHit]:
        return [_FakeHit(message_id=mid) for mid in self._mapping.get(query, [])]


@pytest_asyncio.fixture
async def db_with_qa(db_path: Path) -> Path:
    """state.db with 3 known QA pairs."""
    builder = QASetBuilder(db_path, FakeLLM([]))
    await builder.add_manual("q-rank1?", expected_msg_id=10)
    await builder.add_manual("q-rank5?", expected_msg_id=11)
    await builder.add_manual("q-missing?", expected_msg_id=12)
    return db_path


@pytest.mark.asyncio
async def test_metrics_runner_perfect_score(db_with_qa: Path) -> None:
    fake = FakeRetriever({
        "q-rank1?": [10, 99, 98, 97, 96],          # target at rank 1
        "q-rank5?": [99, 98, 97, 96, 11],          # target at rank 5
        "q-missing?": [99, 98, 97, 96, 95],        # not found
    })
    runner = MetricsRunner(db_with_qa, fake)
    report = await runner.run(top_k=10)
    assert report.qa_set_size == 3
    # q-rank1: hit@1
    # q-rank5: hit@5 only
    # q-missing: 0 everywhere
    assert report.hit_at_1 == pytest.approx(1 / 3)
    assert report.hit_at_5 == pytest.approx(2 / 3)
    assert report.hit_at_10 == pytest.approx(2 / 3)
    expected_mrr = (1.0 + 0.2 + 0.0) / 3
    assert report.mrr == pytest.approx(expected_mrr, abs=1e-6)


@pytest.mark.asyncio
async def test_metrics_runner_persists_to_db(db_with_qa: Path) -> None:
    fake = FakeRetriever({"q-rank1?": [10], "q-rank5?": [11], "q-missing?": []})
    runner = MetricsRunner(
        db_with_qa, fake, config_snapshot={"rrf_weights": [0.5, 0.3, 0.15, 0.05]}
    )
    await runner.run()
    async with aiosqlite.connect(db_with_qa) as conn:
        cur = await conn.execute(
            "SELECT qa_set_size, metrics_json, config_json FROM memory_eval_run"
        )
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == 3
    metrics = json.loads(row[1])
    assert "hit@1" in metrics and "mrr" in metrics
    config = json.loads(row[2])
    assert config["rrf_weights"] == [0.5, 0.3, 0.15, 0.05]


@pytest.mark.asyncio
async def test_metrics_runner_empty_qa_set(tmp_path: Path) -> None:
    db = tmp_path / "empty.db"
    await ensure_v9(db)
    runner = MetricsRunner(db, FakeRetriever({}))
    report = await runner.run()
    assert report.qa_set_size == 0
    assert report.hit_at_1 == 0.0
    assert report.mrr == 0.0


def test_extract_message_id_handles_shapes() -> None:
    @dataclass
    class H:
        message_id: int
    assert _extract_message_id(H(42)) == 42
    assert _extract_message_id({"message_id": 7}) == 7
    assert _extract_message_id({"id": 11}) == 11
    assert _extract_message_id({"foo": "bar"}) is None
    assert _extract_message_id(None) is None
    assert _extract_message_id("garbage") is None


# ----------------------------------------------------------------------
# FeedbackStore
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feedback_store_record_and_summary(db_path: Path) -> None:
    store = FeedbackStore(db_path)
    await store.record(source_msg_id=10, value=1, context_query="q1")
    await store.record(source_msg_id=10, value=1, context_query="q2")
    await store.record(source_msg_id=10, value=-1, context_query="q3")
    summary = await store.summary(source_msg_id=10)
    assert summary == {"up": 2, "down": 1, "net": 1, "total": 3}


@pytest.mark.asyncio
async def test_feedback_store_global_summary(db_path: Path) -> None:
    store = FeedbackStore(db_path)
    await store.record(source_msg_id=10, value=1)
    await store.record(source_msg_id=11, value=-1)
    await store.record(source_msg_id=12, value=-1)
    g = await store.summary()
    assert g == {"up": 1, "down": 2, "net": -1, "total": 3}


@pytest.mark.asyncio
async def test_feedback_store_rejects_invalid_value(db_path: Path) -> None:
    store = FeedbackStore(db_path)
    with pytest.raises(ValueError):
        await store.record(source_msg_id=10, value=0)
    with pytest.raises(ValueError):
        await store.record(source_msg_id=10, value=2)


@pytest.mark.asyncio
async def test_feedback_top_negative(db_path: Path) -> None:
    store = FeedbackStore(db_path)
    # msg 10: 3 downs, 1 up
    # msg 11: 2 downs
    # msg 12: 1 up
    for _ in range(3):
        await store.record(source_msg_id=10, value=-1)
    await store.record(source_msg_id=10, value=1)
    for _ in range(2):
        await store.record(source_msg_id=11, value=-1)
    await store.record(source_msg_id=12, value=1)
    top = await store.top_negative_messages(limit=5, min_down=1)
    assert len(top) == 2
    assert top[0]["source_msg_id"] == 10
    assert top[0]["downs"] == 3
    assert top[1]["source_msg_id"] == 11
    assert top[1]["downs"] == 2
