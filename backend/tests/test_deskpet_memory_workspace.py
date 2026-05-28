# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Phase D — tests for deskpet.memory.workspace.

Coverage:
  * record_action: insert + upsert paths for read/write/edit/delete
  * read action preserves prior content_summary when no new content given
  * write action with content updates hash + summary + byte_size
  * invalid action raises ValueError
  * get / list_session ordering by recency
  * recall finds by path substring + by content_summary
  * recall scoped by session_id excludes other sessions
  * stale_external_edits detects hash mismatch
  * forget_session removes only that session's rows
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
import pytest_asyncio

from deskpet.memory.workspace import WorkspaceMemoryStore, _content_hash
from deskpet.memory.migrator import ensure_v9


@pytest_asyncio.fixture
async def db_path(tmp_path: Path) -> Path:
    db = tmp_path / "state.db"
    await ensure_v9(db)
    return db


@pytest.mark.asyncio
async def test_record_action_insert_write(db_path: Path) -> None:
    store = WorkspaceMemoryStore(db_path)
    await store.record_action(
        session_id="s1", path="src/main.py", action="write",
        content="def main():\n    return 0\n",
    )
    row = await store.get(session_id="s1", path="src/main.py")
    assert row is not None
    assert row["last_action"] == "write"
    assert row["content_hash"] == _content_hash("def main():\n    return 0\n")
    assert row["content_summary"] == "def main():"
    assert row["byte_size"] == len("def main():\n    return 0\n")


@pytest.mark.asyncio
async def test_record_action_read_preserves_summary(db_path: Path) -> None:
    store = WorkspaceMemoryStore(db_path)
    # First write the file (gives us a summary)
    await store.record_action(
        session_id="s1", path="x.py", action="write", content="alpha line\nbeta",
    )
    # Then read it later without supplying content — should keep summary
    await store.record_action(
        session_id="s1", path="x.py", action="read",
    )
    row = await store.get(session_id="s1", path="x.py")
    assert row["last_action"] == "read"
    assert row["content_summary"] == "alpha line"
    # Hash should also be preserved
    assert row["content_hash"] == _content_hash("alpha line\nbeta")


@pytest.mark.asyncio
async def test_record_action_edit_replaces_hash(db_path: Path) -> None:
    store = WorkspaceMemoryStore(db_path)
    await store.record_action(
        session_id="s1", path="x.py", action="write", content="v1",
    )
    h1 = (await store.get(session_id="s1", path="x.py"))["content_hash"]
    await store.record_action(
        session_id="s1", path="x.py", action="edit", content="v2_updated",
    )
    h2 = (await store.get(session_id="s1", path="x.py"))["content_hash"]
    assert h1 != h2
    assert (await store.get(session_id="s1", path="x.py"))["last_action"] == "edit"


@pytest.mark.asyncio
async def test_record_action_invalid_raises(db_path: Path) -> None:
    store = WorkspaceMemoryStore(db_path)
    with pytest.raises(ValueError):
        await store.record_action(
            session_id="s1", path="x.py", action="rename",
        )


@pytest.mark.asyncio
async def test_list_session_ordering(db_path: Path) -> None:
    store = WorkspaceMemoryStore(db_path)
    now = time.time()
    await store.record_action(
        session_id="s1", path="a", action="write", content="x", now=now - 100,
    )
    await store.record_action(
        session_id="s1", path="b", action="write", content="y", now=now - 50,
    )
    await store.record_action(
        session_id="s1", path="c", action="write", content="z", now=now,
    )
    rows = await store.list_session("s1")
    paths = [r["path"] for r in rows]
    assert paths == ["c", "b", "a"]


@pytest.mark.asyncio
async def test_list_session_filters_by_action(db_path: Path) -> None:
    store = WorkspaceMemoryStore(db_path)
    await store.record_action(session_id="s1", path="a", action="write", content="x")
    await store.record_action(session_id="s1", path="b", action="read")
    await store.record_action(session_id="s1", path="c", action="edit", content="y")
    rows = await store.list_session("s1", actions=["write", "edit"])
    paths = {r["path"] for r in rows}
    assert paths == {"a", "c"}


@pytest.mark.asyncio
async def test_recall_by_path_substring(db_path: Path) -> None:
    store = WorkspaceMemoryStore(db_path)
    await store.record_action(
        session_id="s1", path="src/components/Hello.tsx",
        action="write", content="export const Hello = () => <div/>;",
    )
    await store.record_action(
        session_id="s1", path="src/utils/math.ts",
        action="write", content="export const add = (a,b) => a+b;",
    )
    hits = await store.recall("Hello")
    assert len(hits) == 1
    assert hits[0]["path"].endswith("Hello.tsx")


@pytest.mark.asyncio
async def test_recall_by_summary_substring(db_path: Path) -> None:
    store = WorkspaceMemoryStore(db_path)
    await store.record_action(
        session_id="s1", path="a.py",
        action="write",
        content="connect_to_redis_cluster()\nclient = ...",
    )
    hits = await store.recall("redis")
    assert len(hits) == 1
    assert "redis" in hits[0]["content_summary"]


@pytest.mark.asyncio
async def test_recall_session_scoping(db_path: Path) -> None:
    store = WorkspaceMemoryStore(db_path)
    await store.record_action(
        session_id="s1", path="x.py", action="write", content="redis stuff",
    )
    await store.record_action(
        session_id="s2", path="x.py", action="write", content="redis stuff",
    )
    s1_hits = await store.recall("redis", session_id="s1")
    s2_hits = await store.recall("redis", session_id="s2")
    all_hits = await store.recall("redis")
    assert len(s1_hits) == 1
    assert len(s2_hits) == 1
    assert len(all_hits) == 2


@pytest.mark.asyncio
async def test_stale_external_edits_detection(db_path: Path) -> None:
    store = WorkspaceMemoryStore(db_path)
    await store.record_action(
        session_id="s1", path="x.py", action="write", content="original content",
    )
    assert not await store.stale_external_edits(
        session_id="s1", path="x.py", current_content="original content",
    )
    assert await store.stale_external_edits(
        session_id="s1", path="x.py", current_content="someone else changed this",
    )


@pytest.mark.asyncio
async def test_stale_external_edits_unknown_returns_false(db_path: Path) -> None:
    store = WorkspaceMemoryStore(db_path)
    # No prior record → cannot be stale
    assert not await store.stale_external_edits(
        session_id="s1", path="unknown.py", current_content="anything",
    )


@pytest.mark.asyncio
async def test_forget_session(db_path: Path) -> None:
    store = WorkspaceMemoryStore(db_path)
    await store.record_action(session_id="s1", path="a", action="write", content="x")
    await store.record_action(session_id="s2", path="b", action="write", content="y")
    n = await store.forget_session("s1")
    assert n == 1
    assert await store.get(session_id="s1", path="a") is None
    assert await store.get(session_id="s2", path="b") is not None


@pytest.mark.asyncio
async def test_summary_truncation(db_path: Path) -> None:
    store = WorkspaceMemoryStore(db_path)
    long_first_line = "x" * 200
    await store.record_action(
        session_id="s1", path="big.py", action="write",
        content=long_first_line + "\nrest",
    )
    row = await store.get(session_id="s1", path="big.py")
    assert len(row["content_summary"]) == 120
