"""Tests for SqliteConversationMemory + Protocol conformance."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from memory.base import ConversationTurn, MemoryStore
from memory.conversation import SqliteConversationMemory


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_memory.db"


def test_sqlite_memory_satisfies_protocol(tmp_db_path: Path):
    store = SqliteConversationMemory(tmp_db_path)
    assert isinstance(store, MemoryStore)


@pytest.mark.asyncio
async def test_append_and_get_recent_roundtrip(tmp_db_path: Path):
    store = SqliteConversationMemory(tmp_db_path)
    await store.append("s1", "user", "hello")
    await store.append("s1", "assistant", "hi there")

    turns = await store.get_recent("s1")
    assert len(turns) == 2
    assert turns[0].role == "user"
    assert turns[0].content == "hello"
    assert turns[1].role == "assistant"
    assert turns[1].content == "hi there"
    # Chronological order (oldest first)
    assert turns[0].created_at <= turns[1].created_at


@pytest.mark.asyncio
async def test_sessions_are_isolated(tmp_db_path: Path):
    store = SqliteConversationMemory(tmp_db_path)
    await store.append("s1", "user", "s1-msg")
    await store.append("s2", "user", "s2-msg")

    s1_turns = await store.get_recent("s1")
    s2_turns = await store.get_recent("s2")

    assert len(s1_turns) == 1 and s1_turns[0].content == "s1-msg"
    assert len(s2_turns) == 1 and s2_turns[0].content == "s2-msg"


@pytest.mark.asyncio
async def test_get_recent_respects_limit(tmp_db_path: Path):
    store = SqliteConversationMemory(tmp_db_path)
    for i in range(5):
        await store.append("s1", "user", f"msg-{i}")
        # Force different timestamps (SQLite's REAL is subsecond but aiosqlite
        # can batch fast enough that ordering ties appear on fast machines)
        await asyncio.sleep(0.001)

    turns = await store.get_recent("s1", limit=3)
    assert len(turns) == 3
    # Most recent 3 in chronological order: 2, 3, 4
    assert [t.content for t in turns] == ["msg-2", "msg-3", "msg-4"]


@pytest.mark.asyncio
async def test_clear_removes_session_only(tmp_db_path: Path):
    store = SqliteConversationMemory(tmp_db_path)
    await store.append("s1", "user", "s1-msg")
    await store.append("s2", "user", "s2-msg")

    await store.clear("s1")

    assert await store.get_recent("s1") == []
    s2_turns = await store.get_recent("s2")
    assert len(s2_turns) == 1 and s2_turns[0].content == "s2-msg"


@pytest.mark.asyncio
async def test_empty_session_returns_empty_list(tmp_db_path: Path):
    store = SqliteConversationMemory(tmp_db_path)
    turns = await store.get_recent("never-touched")
    assert turns == []


@pytest.mark.asyncio
async def test_parent_dir_auto_created(tmp_path: Path):
    """If db_path's parent doesn't exist, constructor should create it."""
    nested = tmp_path / "deep" / "nested" / "memory.db"
    store = SqliteConversationMemory(nested)
    await store.append("s", "user", "created")
    assert nested.exists()
    turns = await store.get_recent("s")
    assert len(turns) == 1
