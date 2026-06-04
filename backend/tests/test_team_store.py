# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-G1 — TeamStore tests (Companion+Code v2 Multi-Agent Team).

Coverage:
* create / list / get_task happy path
* claim_task atomicity (concurrent claim race — only 1 wins)
* update_task valid + invalid status
* update on missing task returns False
* mailbox send + get_messages mark-read semantics
* permission queue create + grant
* WAL mode actually enabled (smoke check via PRAGMA)
* per-team db isolation (two team_ids → two files)
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from deskpet.agent.team.team_store import (
    TeamStore,
    TeamTask,
    TeamMessage,
    TeamPermissionRequest,
)


@pytest.fixture
def store(tmp_path: Path) -> TeamStore:
    return TeamStore(tmp_path / "teams")


@pytest.mark.asyncio
async def test_create_task_returns_uuid_hex(store: TeamStore) -> None:
    tid = await store.create_task("t1", "do thing A")
    assert isinstance(tid, str)
    assert len(tid) == 32  # uuid4().hex
    assert all(c in "0123456789abcdef" for c in tid)


@pytest.mark.asyncio
async def test_create_then_get_task(store: TeamStore) -> None:
    tid = await store.create_task("t1", "do thing A")
    task = await store.get_task("t1", tid)
    assert task is not None
    assert task.task_id == tid
    assert task.team_id == "t1"
    assert task.description == "do thing A"
    assert task.status == "pending"
    assert task.claimed_by is None
    assert task.created_at > 0


@pytest.mark.asyncio
async def test_list_tasks_empty(store: TeamStore) -> None:
    tasks = await store.list_tasks("t1")
    assert tasks == []


@pytest.mark.asyncio
async def test_list_tasks_status_filter(store: TeamStore) -> None:
    a = await store.create_task("t1", "A")
    b = await store.create_task("t1", "B")
    await store.claim_task("t1", "tm1")  # claims oldest = A
    pending = await store.list_tasks("t1", "pending")
    claimed = await store.list_tasks("t1", "claimed")
    assert [t.task_id for t in pending] == [b]
    assert [t.task_id for t in claimed] == [a]
    all_tasks = await store.list_tasks("t1", "all")
    assert len(all_tasks) == 2


@pytest.mark.asyncio
async def test_claim_returns_none_when_pool_empty(store: TeamStore) -> None:
    task = await store.claim_task("t1", "tm1")
    assert task is None


@pytest.mark.asyncio
async def test_claim_task_marks_claimed_by(store: TeamStore) -> None:
    tid = await store.create_task("t1", "A")
    task = await store.claim_task("t1", "tm-alice")
    assert task is not None
    assert task.task_id == tid
    assert task.status == "claimed"
    assert task.claimed_by == "tm-alice"
    assert task.claimed_at is not None


@pytest.mark.asyncio
async def test_claim_returns_oldest_first(store: TeamStore) -> None:
    a = await store.create_task("t1", "A")
    # tiny pause so created_at differs (resolution is float seconds)
    await asyncio.sleep(0.01)
    b = await store.create_task("t1", "B")
    first = await store.claim_task("t1", "tm1")
    second = await store.claim_task("t1", "tm2")
    assert first is not None and second is not None
    assert first.task_id == a
    assert second.task_id == b


@pytest.mark.asyncio
async def test_concurrent_claim_atomicity(store: TeamStore) -> None:
    """10 coroutines race to claim the SAME single task — exactly 1 wins."""
    tid = await store.create_task("t1", "only one")
    results = await asyncio.gather(
        *[store.claim_task("t1", f"tm{i}") for i in range(10)]
    )
    winners = [r for r in results if r is not None]
    losers = [r for r in results if r is None]
    assert len(winners) == 1, f"expected 1 winner, got {len(winners)}"
    assert len(losers) == 9
    assert winners[0].task_id == tid


@pytest.mark.asyncio
async def test_concurrent_claim_two_tasks_two_winners(store: TeamStore) -> None:
    """2 tasks + 5 claimers → exactly 2 winners; no duplicates."""
    a = await store.create_task("t1", "A")
    b = await store.create_task("t1", "B")
    results = await asyncio.gather(
        *[store.claim_task("t1", f"tm{i}") for i in range(5)]
    )
    won = [r.task_id for r in results if r is not None]
    assert sorted(won) == sorted([a, b])
    assert len(set(won)) == 2  # unique


@pytest.mark.asyncio
async def test_update_task_in_progress(store: TeamStore) -> None:
    tid = await store.create_task("t1", "x")
    await store.claim_task("t1", "tm1")
    ok = await store.update_task("t1", tid, "in_progress")
    assert ok
    task = await store.get_task("t1", tid)
    assert task is not None and task.status == "in_progress"


@pytest.mark.asyncio
async def test_update_task_done_writes_result(store: TeamStore) -> None:
    tid = await store.create_task("t1", "x")
    await store.claim_task("t1", "tm1")
    ok = await store.update_task("t1", tid, "done", "all good")
    assert ok
    task = await store.get_task("t1", tid)
    assert task is not None
    assert task.status == "done"
    assert task.result == "all good"
    assert task.done_at is not None


@pytest.mark.asyncio
async def test_update_task_invalid_status_rejected(store: TeamStore) -> None:
    tid = await store.create_task("t1", "x")
    ok = await store.update_task("t1", tid, "completed")  # not a valid status
    assert ok is False


@pytest.mark.asyncio
async def test_update_task_missing_returns_false(store: TeamStore) -> None:
    ok = await store.update_task("t1", "nope-not-a-real-id", "done", "x")
    assert ok is False


@pytest.mark.asyncio
async def test_send_message_and_read(store: TeamStore) -> None:
    ok = await store.send_message("t1", "tm1", "tm2", "hello")
    assert ok
    msgs = await store.get_messages("t1", "tm2")
    assert len(msgs) == 1
    m = msgs[0]
    assert m.from_id == "tm1"
    assert m.to_id == "tm2"
    assert m.content == "hello"


@pytest.mark.asyncio
async def test_get_messages_marks_unread_then_empty(store: TeamStore) -> None:
    await store.send_message("t1", "tm1", "tm2", "hello")
    first = await store.get_messages("t1", "tm2")
    assert len(first) == 1
    second = await store.get_messages("t1", "tm2")
    # default only_unread=True + mark_read=True → second call sees nothing
    assert second == []


@pytest.mark.asyncio
async def test_get_messages_no_markread_keeps_unread(store: TeamStore) -> None:
    await store.send_message("t1", "tm1", "tm2", "hello")
    first = await store.get_messages("t1", "tm2", mark_read=False)
    second = await store.get_messages("t1", "tm2", mark_read=False)
    assert len(first) == 1 and len(second) == 1


@pytest.mark.asyncio
async def test_request_permission_returns_id(store: TeamStore) -> None:
    rid = await store.request_permission("t1", "tm1", "write_file")
    assert isinstance(rid, int)
    assert rid > 0
    req = await store.get_permission("t1", rid)
    assert req is not None
    assert req.teammate_id == "tm1"
    assert req.action == "write_file"
    assert req.granted is None
    assert req.decided_at is None


@pytest.mark.asyncio
async def test_grant_permission_allow_and_deny(store: TeamStore) -> None:
    rid_a = await store.request_permission("t1", "tm1", "write_file")
    rid_b = await store.request_permission("t1", "tm2", "bash_run")
    ok_a = await store.grant_permission("t1", rid_a, allow=True)
    ok_b = await store.grant_permission("t1", rid_b, allow=False)
    assert ok_a and ok_b
    req_a = await store.get_permission("t1", rid_a)
    req_b = await store.get_permission("t1", rid_b)
    assert req_a is not None and req_a.granted is True
    assert req_b is not None and req_b.granted is False
    assert req_a.decided_at is not None and req_b.decided_at is not None


@pytest.mark.asyncio
async def test_grant_permission_idempotent_blocks_second_decision(
    store: TeamStore,
) -> None:
    rid = await store.request_permission("t1", "tm1", "write_file")
    assert await store.grant_permission("t1", rid, allow=True) is True
    # Second decision should be rejected (rowcount=0 because WHERE
    # granted IS NULL no longer matches).
    assert await store.grant_permission("t1", rid, allow=False) is False
    req = await store.get_permission("t1", rid)
    assert req is not None and req.granted is True


@pytest.mark.asyncio
async def test_per_team_db_isolation(store: TeamStore, tmp_path: Path) -> None:
    """Two team_ids → two separate .db files; no cross-leak."""
    a_tid = await store.create_task("team-A", "A-work")
    b_tid = await store.create_task("team-B", "B-work")
    a_tasks = await store.list_tasks("team-A")
    b_tasks = await store.list_tasks("team-B")
    assert [t.task_id for t in a_tasks] == [a_tid]
    assert [t.task_id for t in b_tasks] == [b_tid]
    # Both files exist
    base = tmp_path / "teams"
    assert (base / "team-A.db").exists()
    assert (base / "team-B.db").exists()


@pytest.mark.asyncio
async def test_db_uses_wal_journal_mode(store: TeamStore, tmp_path: Path) -> None:
    """Smoke: PRAGMA journal_mode reports 'wal' after ensure_schema."""
    await store.create_task("t1", "x")  # triggers ensure_schema
    db_path = tmp_path / "teams" / "t1.db"
    # Use sync sqlite3 to interrogate — easier than async for a pragma.
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("PRAGMA journal_mode")
        mode = cur.fetchone()[0]
    finally:
        conn.close()
    assert str(mode).lower() == "wal"


def test_team_id_rejects_path_traversal(tmp_path: Path) -> None:
    store = TeamStore(tmp_path)
    with pytest.raises(ValueError):
        store._db_path("../escape")
    with pytest.raises(ValueError):
        store._db_path("a/b")
    with pytest.raises(ValueError):
        store._db_path("c\\d")


def test_team_task_to_dict_round_trips_fields() -> None:
    t = TeamTask(
        task_id="abc",
        team_id="t1",
        description="x",
        status="done",
        created_at=1.0,
        claimed_by="tm1",
        result="r",
        claimed_at=2.0,
        done_at=3.0,
    )
    d = t.to_dict()
    assert d["task_id"] == "abc"
    assert d["status"] == "done"
    assert d["result"] == "r"
    assert d["claimed_at"] == 2.0


def test_team_message_and_permission_to_dict() -> None:
    m = TeamMessage(
        msg_id=1,
        team_id="t1",
        from_id="tm1",
        to_id="tm2",
        content="hi",
        ts=1.0,
        read_flag=True,
    )
    assert m.to_dict()["content"] == "hi"
    p = TeamPermissionRequest(
        req_id=2,
        team_id="t1",
        teammate_id="tm1",
        action="bash",
        created_at=1.0,
        granted=True,
        decided_at=2.0,
    )
    assert p.to_dict()["granted"] is True
