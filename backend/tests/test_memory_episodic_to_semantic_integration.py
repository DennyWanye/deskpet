"""TG-S5 — episodic→semantic 固化通路集成测试（Stage 2 / WI-S2.4）。

TDD §TG-S5 (TS5-1 ~ TS5-10)：summarize_old_sessions 跑完后，flag 开
时 fact_extractor 异步抽 summary 文本，落 facts 表 category='episodic_summary'。
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import aiosqlite
import pytest

from deskpet.memory.memory_v2_schema import (
    ensure_memory_v2_tables,
    _reset_cache_for_tests,
)
from deskpet.memory.schema_v2_migrator import _reset_failures_for_tests
from deskpet.memory.facts import (
    FactsStore, FactExtractor, VALID_CATEGORIES, _CATEGORY_DECAY,
)
from deskpet.memory.summarizer import summarize_old_sessions
from deskpet.memory.session_db import SessionDB


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    _reset_cache_for_tests()
    _reset_failures_for_tests()
    return tmp_path / "state.db"


# ---------------------------------------------------------------------------
# TS5-10 — VALID_CATEGORIES 含 episodic_summary（防 E5 倒退）
# ---------------------------------------------------------------------------
def test_ts5_10_valid_categories_includes_episodic_summary() -> None:
    assert "episodic_summary" in VALID_CATEGORIES
    assert "episodic_summary" in _CATEGORY_DECAY
    # 衰减率应"慢"（episodic summary 需要长期保留）
    assert _CATEGORY_DECAY["episodic_summary"] <= 0.02


# ---------------------------------------------------------------------------
# TS5-4 — process_message(role=system, source=user_message) → 返 []
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ts5_4_role_system_blocked_without_summarizer_source(db_path: Path) -> None:
    await ensure_memory_v2_tables(db_path)
    store = FactsStore(db_path)
    called = []

    async def llm(p: str) -> str:
        called.append(p)
        return "[]"

    ext = FactExtractor(store, extract_llm=llm)
    res = await ext.process_message(
        message_id=1, content="some long content here for sure",
        role="system", source="user_message",
    )
    assert res == []
    # LLM extract 都不应被调
    assert called == []


# ---------------------------------------------------------------------------
# TS5-5 — process_message(role=system, source=summarizer)
#   → 走抽取链路；category 强制 override 为 episodic_summary
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ts5_5_summarizer_source_overrides_category(db_path: Path) -> None:
    await ensure_memory_v2_tables(db_path)
    store = FactsStore(db_path)

    async def llm(p: str) -> str:
        return json.dumps([
            {
                "category": "preference",  # 故意非 episodic_summary
                "subject": "user",
                "key": "loves_winter",
                "value": "I love winter",
                "confidence": 0.9,
                "evidence": "I love winter",
            },
        ])

    ext = FactExtractor(store, extract_llm=llm)
    res = await ext.process_message(
        message_id=99, content="summary text spanning many chars here",
        role="system", source="summarizer",
    )
    assert len(res) == 1
    # category 应被改写
    assert res[0]["category"] == "episodic_summary"
    # DB 中也是
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute("SELECT category FROM facts WHERE id = ?", (res[0]["id"],))
        r = await cur.fetchone()
    assert r[0] == "episodic_summary"


# ---------------------------------------------------------------------------
# TS5-1 ~ TS5-3 — summarize_old_sessions 端到端：跑 summary → background task
# 抽出 episodic_summary fact
# ---------------------------------------------------------------------------
async def _seed_old_session(
    db_path: Path,
    session_id: str,
    n_messages: int,
    cutoff_ts: float,
) -> None:
    """构造 n 条 user/assistant 消息，全部 created_at < cutoff_ts。"""
    sdb = SessionDB(db_path=db_path)
    await sdb.initialize()
    # 直接 SQL INSERT 以控制 created_at（append_message 用 time.time()）
    async with aiosqlite.connect(db_path) as conn:
        for i in range(n_messages):
            await conn.execute(
                "INSERT INTO messages (session_id, role, content, created_at, "
                "salience, decay_last_touch) VALUES (?, ?, ?, ?, 0.5, ?)",
                (
                    session_id,
                    "user" if i % 2 == 0 else "assistant",
                    f"Message #{i} — user really loves winter and tea",
                    cutoff_ts - 86400.0 - i,
                    cutoff_ts - 86400.0 - i,
                ),
            )
        await conn.commit()


@pytest.mark.asyncio
async def test_ts5_1_episodic_flag_creates_background_task(db_path: Path) -> None:
    await ensure_memory_v2_tables(db_path)
    cutoff_age_days = 30
    cutoff_ts = time.time() - cutoff_age_days * 86400 - 60
    await _seed_old_session(db_path, "old-session-1", 25, cutoff_ts)

    async def summarize_llm(messages):
        return {"content": "User strongly prefers winter and tea over coffee."}

    async def fact_llm(p: str) -> str:
        return json.dumps([{
            "category": "preference", "subject": "user",
            "key": "season_preference", "value": "winter",
            "confidence": 0.9, "evidence": "user really loves winter",
        }])

    store = FactsStore(db_path)
    ext = FactExtractor(store, extract_llm=fact_llm, min_chars=4)

    bg: set[asyncio.Task] = set()
    result = await summarize_old_sessions(
        db_path,
        summarize_llm,
        age_days=cutoff_age_days,
        min_messages=20,
        max_per_run=10,
        fact_extractor=ext,
        episodic_to_semantic=True,
        background_tasks=bg,
    )

    assert result.sessions_summarized == 1
    # background task 加进了 set
    assert len(bg) >= 1

    # TS5-2：等 task 完成 → 应有 ≥1 条 episodic_summary fact
    await asyncio.gather(*list(bg), return_exceptions=True)
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM facts WHERE category = 'episodic_summary'"
        )
        cnt = (await cur.fetchone())[0]
    assert cnt >= 1


# ---------------------------------------------------------------------------
# TS5-3 — episodic_to_semantic=False → 不抽 episodic facts
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ts5_3_flag_off_no_episodic_facts(db_path: Path) -> None:
    await ensure_memory_v2_tables(db_path)
    cutoff_age_days = 30
    cutoff_ts = time.time() - cutoff_age_days * 86400 - 60
    await _seed_old_session(db_path, "old-session-2", 25, cutoff_ts)

    async def summarize_llm(messages):
        return {"content": "User likes tea."}

    async def fact_llm(p: str) -> str:
        return json.dumps([{
            "category": "preference", "subject": "user", "key": "tea",
            "value": "likes tea", "confidence": 0.9, "evidence": "tea",
        }])

    store = FactsStore(db_path)
    ext = FactExtractor(store, extract_llm=fact_llm, min_chars=4)
    bg: set[asyncio.Task] = set()
    await summarize_old_sessions(
        db_path,
        summarize_llm,
        age_days=cutoff_age_days,
        min_messages=20,
        max_per_run=10,
        fact_extractor=ext,
        episodic_to_semantic=False,  # ★ flag off
        background_tasks=bg,
    )
    # 不应有 background task
    assert len(bg) == 0
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM facts WHERE category = 'episodic_summary'"
        )
        cnt = (await cur.fetchone())[0]
    assert cnt == 0


# ---------------------------------------------------------------------------
# TS5-7 — LLM 对 summary 抽取 fail → 主流程不受影响
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ts5_7_fact_llm_failure_isolated(db_path: Path) -> None:
    await ensure_memory_v2_tables(db_path)
    cutoff_age_days = 30
    cutoff_ts = time.time() - cutoff_age_days * 86400 - 60
    await _seed_old_session(db_path, "session-fail", 25, cutoff_ts)

    async def summarize_llm(messages):
        return {"content": "User likes hiking."}

    async def fact_llm(p: str) -> str:
        raise RuntimeError("simulated LLM failure")

    store = FactsStore(db_path)
    ext = FactExtractor(store, extract_llm=fact_llm, min_chars=4)
    bg: set[asyncio.Task] = set()
    result = await summarize_old_sessions(
        db_path, summarize_llm,
        age_days=cutoff_age_days,
        min_messages=20, max_per_run=10,
        fact_extractor=ext,
        episodic_to_semantic=True,
        background_tasks=bg,
    )
    # summary 主流程仍成功
    assert result.sessions_summarized == 1
    # 等 background task 完成
    await asyncio.gather(*list(bg), return_exceptions=True)


# ---------------------------------------------------------------------------
# TS5-8 ★v2 — add_done_callback 触发：完成后 set 自动 discard
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ts5_8_done_callback_discards_task(db_path: Path) -> None:
    await ensure_memory_v2_tables(db_path)
    cutoff_age_days = 30
    cutoff_ts = time.time() - cutoff_age_days * 86400 - 60
    await _seed_old_session(db_path, "sess-cb", 25, cutoff_ts)

    async def summarize_llm(messages):
        return {"content": "Some summary text."}

    async def fact_llm(p: str) -> str:
        return "[]"  # 不抽出 facts，task 快速完成

    store = FactsStore(db_path)
    ext = FactExtractor(store, extract_llm=fact_llm, min_chars=4)
    bg: set[asyncio.Task] = set()
    await summarize_old_sessions(
        db_path, summarize_llm,
        age_days=cutoff_age_days,
        min_messages=20, max_per_run=10,
        fact_extractor=ext,
        episodic_to_semantic=True,
        background_tasks=bg,
    )
    # bg set 短暂含 task；等完成后应 discard 干净
    await asyncio.gather(*list(bg), return_exceptions=True)
    # 让事件循环跑一周期触发 done callback
    await asyncio.sleep(0)
    assert len(bg) == 0


# ---------------------------------------------------------------------------
# TS5-9 ★v2 — shutdown gather 等多个 pending tasks 完成
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ts5_9_shutdown_gather_waits_all(db_path: Path) -> None:
    await ensure_memory_v2_tables(db_path)
    bg: set[asyncio.Task] = set()
    done_count = 0

    async def slow_task(i: int) -> None:
        nonlocal done_count
        await asyncio.sleep(0.05)
        done_count += 1

    for i in range(5):
        t = asyncio.create_task(slow_task(i))
        bg.add(t)
        t.add_done_callback(bg.discard)

    # 模拟 shutdown
    await asyncio.gather(*list(bg), return_exceptions=True)
    await asyncio.sleep(0)
    assert done_count == 5
    assert len(bg) == 0
