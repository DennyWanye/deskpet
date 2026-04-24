"""P4-S2 task 4.3 — SessionDB.on_message_written hook 集成测试。

契约验证：
  * 无 hook（None）时 append_message 行为和 S1 完全一致
  * hook 被调用时参数正确（msg_id + content），且 msg_id 与返回值一致
  * hook 抛异常不影响 append_message 返回值（failure 只 log）
  * hook 是 async 的，需要被 await（同步错调用 → wraps 到 log）
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from deskpet.memory.session_db import SessionDB


# ---- 无 hook：回归 S1 -------------------------------------------------


@pytest_asyncio.fixture
async def plain_db(tmp_path: Path):
    db = SessionDB(tmp_path / "state.db")  # on_message_written=None
    await db.initialize()
    yield db
    await db.close()


@pytest.mark.asyncio
async def test_append_message_works_without_hook(plain_db: SessionDB):
    """无 hook 时 append_message 行为与 S1 一致。"""
    sid = await plain_db.create_session()
    mid = await plain_db.append_message(sid, "user", "no-hook test")
    assert isinstance(mid, int) and mid > 0
    msgs = await plain_db.get_messages(sid)
    assert len(msgs) == 1
    assert msgs[0]["content"] == "no-hook test"


# ---- 带 hook：基本调用契约 -------------------------------------------


@pytest.mark.asyncio
async def test_hook_receives_msg_id_and_content(tmp_path: Path):
    seen: list[tuple[int, str]] = []

    async def hook(msg_id: int, content: str) -> None:
        seen.append((msg_id, content))

    db = SessionDB(tmp_path / "state.db", on_message_written=hook)
    await db.initialize()
    try:
        sid = await db.create_session()
        mid = await db.append_message(sid, "user", "hello hook")
        assert seen == [(mid, "hello hook")]

        # 第二条，确认 msg_id 递增
        mid2 = await db.append_message(sid, "assistant", "response")
        assert len(seen) == 2
        assert seen[1] == (mid2, "response")
        assert mid2 > mid
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_hook_failure_does_not_affect_return_value(tmp_path: Path):
    """hook 抛异常不影响 append_message 返回值（failure 只 log warn）。"""

    async def bad_hook(msg_id: int, content: str) -> None:
        raise RuntimeError("simulated hook error")

    db = SessionDB(tmp_path / "state.db", on_message_written=bad_hook)
    await db.initialize()
    try:
        sid = await db.create_session()
        # 应该正常返回 msg_id，不抛
        mid = await db.append_message(sid, "user", "content despite bad hook")
        assert isinstance(mid, int) and mid > 0

        # 确认消息真的写入了（hook 失败不影响主写）
        msgs = await db.get_messages(sid)
        assert len(msgs) == 1
        assert msgs[0]["content"] == "content despite bad hook"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_hook_called_exactly_once_per_message(tmp_path: Path):
    """hook 每条 message 只被调一次（不会因为 retry 等重复触发）。"""
    call_count = {"n": 0}

    async def counting_hook(msg_id: int, content: str) -> None:
        call_count["n"] += 1

    db = SessionDB(tmp_path / "state.db", on_message_written=counting_hook)
    await db.initialize()
    try:
        sid = await db.create_session()
        for i in range(5):
            await db.append_message(sid, "user", f"m-{i}")
        assert call_count["n"] == 5
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_hook_runs_after_commit(tmp_path: Path):
    """hook 调用时 msg_id 对应的行已经落盘（hook 在写锁外且 commit 后）。

    在 hook 内部用独立连接读 messages 表，应能读到这条新消息。
    """

    async def verifying_hook(msg_id: int, content: str) -> None:
        # 在 hook 里用新 SessionDB 实例读同一 DB
        import aiosqlite

        async with aiosqlite.connect(db._db_path) as conn:
            cursor = await conn.execute(
                "SELECT content FROM messages WHERE id = ?", (msg_id,)
            )
            row = await cursor.fetchone()
            await cursor.close()
        assert row is not None, f"msg {msg_id} not found in hook"
        assert row[0] == content

    db = SessionDB(tmp_path / "state.db", on_message_written=verifying_hook)
    await db.initialize()
    try:
        sid = await db.create_session()
        await db.append_message(sid, "user", "visible-from-hook")
    finally:
        await db.close()


# ---- 非常规：hook 是 coroutine factory ---------------------------------


@pytest.mark.asyncio
async def test_hook_can_do_async_work(tmp_path: Path):
    """hook 是真 async，可以 await 内部 asyncio.sleep 等；主路径会等它完成。"""
    events: list[str] = []

    async def slow_hook(msg_id: int, content: str) -> None:
        await asyncio.sleep(0.05)
        events.append(f"hook:{msg_id}")

    db = SessionDB(tmp_path / "state.db", on_message_written=slow_hook)
    await db.initialize()
    try:
        sid = await db.create_session()
        events.append("before-append")
        mid = await db.append_message(sid, "user", "async hook")
        events.append("after-append")
        # after-append 应该在 hook 完成之后（hook 是 await 的，不是 fire-and-forget）
        assert events == ["before-append", f"hook:{mid}", "after-append"]
    finally:
        await db.close()
