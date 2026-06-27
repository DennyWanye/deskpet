# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S20-D summarizer 单测。

覆盖：
  - 候选 session 筛选（age/min_messages/已总结过的排除）
  - 单 session 总结全流程（拉原文 → LLM mock → 写 archive + summary）
  - 事务原子性（部分失败时回滚）
  - 输入截断（超长 conversation 截头尾）
  - 空 summary 不入库
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import aiosqlite

from deskpet.memory.session_db import SessionDB
from deskpet.memory.summarizer import (
    SummaryResult,
    summarize_old_sessions,
)


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


@pytest_asyncio.fixture
async def fresh_db(tmp_path: Path):
    """A fresh state.db with v10 schema."""
    db_path = tmp_path / "state.db"
    sdb = SessionDB(db_path=db_path)
    await sdb.initialize()
    yield db_path, sdb
    await sdb.close()


def _stub_llm(reply: str = "测试摘要：用户喜欢喝冰可乐。"):
    """A stub LLMCall that returns a fixed reply."""
    async def _call(messages: list[dict[str, str]]) -> dict[str, Any]:
        return {"content": reply}
    return _call


def _make_failing_llm(exc_class=RuntimeError):
    async def _call(messages):
        raise exc_class("simulated LLM failure")
    return _call


async def _seed_session(
    sdb: SessionDB, session_id: str, n_messages: int, age_days: float
):
    """Seed a session with N messages, all created `age_days` ago."""
    old_ts = time.time() - age_days * 86400
    for i in range(n_messages):
        await sdb.append_message(
            session_id=session_id,
            role="user" if i % 2 == 0 else "assistant",
            content=f"old msg {i} in {session_id}",
        )
    # Manually backdate created_at
    async with aiosqlite.connect(sdb._db_path) as db:
        await db.execute(
            "UPDATE messages SET created_at = ? WHERE session_id = ?",
            (old_ts, session_id),
        )
        await db.commit()


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_candidates_returns_empty_result(fresh_db):
    db_path, sdb = fresh_db
    result = await summarize_old_sessions(
        db_path=db_path,
        llm_call=_stub_llm(),
    )
    assert result.sessions_scanned == 0
    assert result.sessions_summarized == 0


@pytest.mark.asyncio
async def test_session_too_few_messages_skipped(fresh_db):
    """Session with < min_messages 不进候选名单。"""
    db_path, sdb = fresh_db
    await _seed_session(sdb, "s1", n_messages=5, age_days=60)
    result = await summarize_old_sessions(
        db_path=db_path,
        llm_call=_stub_llm(),
        min_messages=20,
    )
    assert result.sessions_scanned == 0


@pytest.mark.asyncio
async def test_recent_session_skipped(fresh_db):
    """全部消息在 cutoff 之内的 session 不进候选。"""
    db_path, sdb = fresh_db
    await _seed_session(sdb, "s1", n_messages=25, age_days=5)  # 5天前
    result = await summarize_old_sessions(
        db_path=db_path,
        llm_call=_stub_llm(),
        age_days=30,
    )
    assert result.sessions_scanned == 0


@pytest.mark.asyncio
async def test_full_round_trip_one_session(fresh_db):
    """老 session 应该被总结，原文搬到 archive，summary 入主表。"""
    db_path, sdb = fresh_db
    await _seed_session(sdb, "s1", n_messages=25, age_days=60)

    result = await summarize_old_sessions(
        db_path=db_path,
        llm_call=_stub_llm(reply="用户提到了苹果和拉面。"),
        age_days=30,
        min_messages=20,
    )

    assert result.sessions_scanned == 1
    assert result.sessions_summarized == 1
    assert result.messages_archived == 25
    assert len(result.summaries_created) == 1

    # Verify side-effects in db
    conn = sqlite3.connect(str(db_path))
    try:
        # 原文应该全部从 messages 删了
        (n_main,) = conn.execute(
            "SELECT count(*) FROM messages WHERE session_id = 's1' AND COALESCE(is_summary, 0) = 0"
        ).fetchone()
        assert n_main == 0, "原文应该全部归档了"

        # summary 应该在 messages 主表
        (n_summary,) = conn.execute(
            "SELECT count(*) FROM messages WHERE session_id = 's1' AND is_summary = 1"
        ).fetchone()
        assert n_summary == 1

        # 原文应该在 archive
        (n_arch,) = conn.execute(
            "SELECT count(*) FROM messages_archive WHERE session_id = 's1'"
        ).fetchone()
        assert n_arch == 25

        # archived_into_id 应该都指向 summary
        summary_id = result.summaries_created[0]
        (n_linked,) = conn.execute(
            "SELECT count(*) FROM messages_archive WHERE archived_into_id = ?",
            (summary_id,),
        ).fetchone()
        assert n_linked == 25

        # summary 内容 + summary_of JSON
        row = conn.execute(
            "SELECT content, summary_of FROM messages WHERE id = ?",
            (summary_id,),
        ).fetchone()
        assert "用户提到了苹果和拉面" in row[0]
        archived_ids = json.loads(row[1])
        assert len(archived_ids) == 25
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_summary_session_not_resummarized(fresh_db):
    """已经存在 summary 的 session 不应被再次扫到。"""
    db_path, sdb = fresh_db
    await _seed_session(sdb, "s1", n_messages=25, age_days=60)
    # 第一次总结
    result1 = await summarize_old_sessions(
        db_path=db_path, llm_call=_stub_llm(), age_days=30, min_messages=20,
    )
    assert result1.sessions_summarized == 1

    # 再跑一次 — 不该再总结
    result2 = await summarize_old_sessions(
        db_path=db_path, llm_call=_stub_llm(), age_days=30, min_messages=20,
    )
    assert result2.sessions_scanned == 0


@pytest.mark.asyncio
async def test_empty_llm_response_skips_session(fresh_db):
    """LLM 返回空摘要 → 不写 archive 不删原文。"""
    db_path, sdb = fresh_db
    await _seed_session(sdb, "s1", n_messages=25, age_days=60)
    result = await summarize_old_sessions(
        db_path=db_path,
        llm_call=_stub_llm(reply="   "),  # 空白
        age_days=30, min_messages=20,
    )
    assert result.sessions_scanned == 1
    assert result.sessions_summarized == 0
    assert result.messages_archived == 0

    conn = sqlite3.connect(str(db_path))
    try:
        (n_main,) = conn.execute(
            "SELECT count(*) FROM messages WHERE session_id = 's1'"
        ).fetchone()
        assert n_main == 25, "LLM 失败时原文必须保留"
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_llm_failure_doesnt_corrupt(fresh_db):
    """LLM 抛异常 → 原文完整保留，错误进 result.errors。"""
    db_path, sdb = fresh_db
    await _seed_session(sdb, "s1", n_messages=25, age_days=60)
    result = await summarize_old_sessions(
        db_path=db_path,
        llm_call=_make_failing_llm(),
        age_days=30, min_messages=20,
    )
    assert result.sessions_scanned == 1
    assert result.sessions_summarized == 0
    assert len(result.errors) == 1
    assert "simulated LLM failure" in result.errors[0]

    # 原文必须完整
    conn = sqlite3.connect(str(db_path))
    try:
        (n_main,) = conn.execute(
            "SELECT count(*) FROM messages WHERE session_id = 's1'"
        ).fetchone()
        assert n_main == 25
        (n_arch,) = conn.execute(
            "SELECT count(*) FROM messages_archive WHERE session_id = 's1'"
        ).fetchone()
        assert n_arch == 0
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_max_per_run_caps_processing(fresh_db):
    """3 个 session 都候选，max_per_run=2 只处理 2 个。"""
    db_path, sdb = fresh_db
    for sid in ("a", "b", "c"):
        await _seed_session(sdb, sid, n_messages=25, age_days=60)
    result = await summarize_old_sessions(
        db_path=db_path,
        llm_call=_stub_llm(),
        age_days=30, min_messages=20,
        max_per_run=2,
    )
    assert result.sessions_scanned == 2
    assert result.sessions_summarized == 2


@pytest.mark.asyncio
async def test_summary_enqueued_to_vector_worker(fresh_db):
    """Pass-through: summary 必须被 enqueue 到 vector_worker，否则召回缺失。"""
    db_path, sdb = fresh_db
    await _seed_session(sdb, "s1", n_messages=25, age_days=60)

    enqueued: list[tuple[int, str]] = []

    class _StubVecWorker:
        async def enqueue(self, message_id: int, text: str) -> None:
            enqueued.append((message_id, text))

    result = await summarize_old_sessions(
        db_path=db_path,
        llm_call=_stub_llm(reply="测试摘要"),
        age_days=30, min_messages=20,
        vector_worker=_StubVecWorker(),
    )
    assert result.sessions_summarized == 1
    assert len(enqueued) == 1
    summary_id_enqueued, text_enqueued = enqueued[0]
    assert summary_id_enqueued == result.summaries_created[0]
    assert "测试摘要" in text_enqueued


@pytest.mark.asyncio
async def test_long_conversation_truncated(fresh_db):
    """超长 conversation 应该截头尾，不会让 LLM 调用挂掉。"""
    db_path, sdb = fresh_db
    # 50 条长消息
    long_text = "x" * 1000
    for i in range(50):
        await sdb.append_message(
            session_id="long",
            role="user" if i % 2 == 0 else "assistant",
            content=f"{long_text} {i}",
        )
    old_ts = time.time() - 60 * 86400
    async with aiosqlite.connect(sdb._db_path) as db:
        await db.execute(
            "UPDATE messages SET created_at = ? WHERE session_id = 'long'",
            (old_ts,),
        )
        await db.commit()

    captured_prompts: list[str] = []

    async def _capturing_llm(messages):
        captured_prompts.append(messages[-1]["content"])
        return {"content": "done"}

    result = await summarize_old_sessions(
        db_path=db_path,
        llm_call=_capturing_llm,
        age_days=30, min_messages=20,
        max_input_chars=5000,
    )
    assert result.sessions_summarized == 1
    # captured prompt 不应该比 max_input_chars + prompt boilerplate 大太多
    captured_len = len(captured_prompts[0])
    assert captured_len < 6000, f"prompt 应该被截断，实际长度 {captured_len}"
    # 应该包含截断标记
    assert "省略中段" in captured_prompts[0]
