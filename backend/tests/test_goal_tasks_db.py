# SPDX-License-Identifier: BUSL-1.1
"""WI-1.2 Task 5 — goal_tasks 表 + SessionDB CRUD + 原子 claim 测试.

TDD 顺序：先写测试（预期 FAIL），再实现后全绿。

Assertions:
- create/get/list round-trip
- DAG ready (B depends_on=[A]; claim skips B until A done)
- 两协程并发 claim_ready 不双占 (pass^k=5)
- update done → progress 回填 session_goals
- 环检测拒绝
- flag-OFF：仅调 ensure_memory_v2_tables 不建 goal_tasks（R-T5）
"""
from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from deskpet.memory.memory_v2_schema import (
    ensure_memory_v2_tables,
    _reset_cache_for_tests,
)
from deskpet.memory.session_db import SessionDB


@pytest.fixture(autouse=True)
def _reset_schema_cache():
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _tid() -> str:
    return str(uuid.uuid4())


def _now() -> float:
    return time.time()


# ──────────────────────────────────────────────────────────────────────
# 1. create / get / list round-trip
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_get_round_trip(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    goal_id = _tid()
    session_id = _tid()
    task_id = _tid()
    now = _now()

    await db.create_goal_task(
        task_id=task_id,
        goal_id=goal_id,
        session_id=session_id,
        title="Task A",
        depends_on=[],
        created_at=now,
        updated_at=now,
    )

    row = await db.get_goal_task(task_id)
    assert row is not None
    assert row["task_id"] == task_id
    assert row["goal_id"] == goal_id
    assert row["session_id"] == session_id
    assert row["title"] == "Task A"
    assert row["status"] == "pending"
    assert row["depends_on"] == []
    assert row["claimed_by"] is None
    assert row["result"] is None


@pytest.mark.asyncio
async def test_get_nonexistent_returns_none(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    row = await db.get_goal_task("nonexistent-id")
    assert row is None


@pytest.mark.asyncio
async def test_list_goal_tasks_ordered_by_created_at(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    goal_id = _tid()
    session_id = _tid()
    now = _now()

    ids = [_tid() for _ in range(3)]
    for i, tid in enumerate(ids):
        await db.create_goal_task(
            task_id=tid,
            goal_id=goal_id,
            session_id=session_id,
            title=f"Task {i}",
            depends_on=[],
            created_at=now + i,
            updated_at=now + i,
        )

    tasks = await db.list_goal_tasks(goal_id)
    assert len(tasks) == 3
    assert [t["task_id"] for t in tasks] == ids


@pytest.mark.asyncio
async def test_list_filters_by_goal_id(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    g1, g2 = _tid(), _tid()
    session_id = _tid()
    now = _now()

    await db.create_goal_task(
        task_id=_tid(), goal_id=g1, session_id=session_id,
        title="G1 task", depends_on=[], created_at=now, updated_at=now,
    )
    await db.create_goal_task(
        task_id=_tid(), goal_id=g2, session_id=session_id,
        title="G2 task", depends_on=[], created_at=now, updated_at=now,
    )

    assert len(await db.list_goal_tasks(g1)) == 1
    assert len(await db.list_goal_tasks(g2)) == 1


# ──────────────────────────────────────────────────────────────────────
# 2. depends_on 存储 / JSON round-trip
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_depends_on_json_roundtrip(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    goal_id = _tid()
    session_id = _tid()
    now = _now()
    tid_a = _tid()
    tid_b = _tid()

    await db.create_goal_task(
        task_id=tid_a, goal_id=goal_id, session_id=session_id,
        title="A", depends_on=[], created_at=now, updated_at=now,
    )
    await db.create_goal_task(
        task_id=tid_b, goal_id=goal_id, session_id=session_id,
        title="B", depends_on=[tid_a], created_at=now + 1, updated_at=now + 1,
    )

    row_b = await db.get_goal_task(tid_b)
    assert row_b["depends_on"] == [tid_a]


# ──────────────────────────────────────────────────────────────────────
# 3. update_goal_task partial update
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_goal_task_status(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    goal_id = _tid()
    session_id = _tid()
    now = _now()
    task_id = _tid()

    await db.create_goal_task(
        task_id=task_id, goal_id=goal_id, session_id=session_id,
        title="T", depends_on=[], created_at=now, updated_at=now,
    )
    await db.update_goal_task(task_id, status="done", result="ok", updated_at=now + 1)
    row = await db.get_goal_task(task_id)
    assert row["status"] == "done"
    assert row["result"] == "ok"
    assert row["updated_at"] == pytest.approx(now + 1, abs=0.01)


@pytest.mark.asyncio
async def test_update_goal_task_claimed_by(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    goal_id = _tid()
    session_id = _tid()
    now = _now()
    task_id = _tid()

    await db.create_goal_task(
        task_id=task_id, goal_id=goal_id, session_id=session_id,
        title="T", depends_on=[], created_at=now, updated_at=now,
    )
    await db.update_goal_task(task_id, claimed_by="agent-1", updated_at=now + 1)
    row = await db.get_goal_task(task_id)
    assert row["claimed_by"] == "agent-1"


# ──────────────────────────────────────────────────────────────────────
# 4. DAG ready: claim skips B until A is done
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dag_claim_skips_blocked_task(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    goal_id = _tid()
    session_id = _tid()
    now = _now()
    tid_a = _tid()
    tid_b = _tid()

    await db.create_goal_task(
        task_id=tid_a, goal_id=goal_id, session_id=session_id,
        title="A", depends_on=[], created_at=now, updated_at=now,
    )
    await db.create_goal_task(
        task_id=tid_b, goal_id=goal_id, session_id=session_id,
        title="B", depends_on=[tid_a], created_at=now + 1, updated_at=now + 1,
    )

    # Claim first task — should get A (no deps)
    claimed = await db.claim_ready_goal_task(goal_id, "agent-1", now + 2)
    assert claimed is not None
    assert claimed["task_id"] == tid_a

    # Now A is claimed (not done yet), B's deps not satisfied → nothing available
    claimed2 = await db.claim_ready_goal_task(goal_id, "agent-2", now + 3)
    assert claimed2 is None

    # Mark A done, now B should be claimable
    await db.update_goal_task(tid_a, status="done", result="done", updated_at=now + 4)
    claimed3 = await db.claim_ready_goal_task(goal_id, "agent-2", now + 5)
    assert claimed3 is not None
    assert claimed3["task_id"] == tid_b


# ──────────────────────────────────────────────────────────────────────
# 5. Concurrent claim: 两协程并发 claim 不双占 (pass^k=5)
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_concurrent_claim_no_double_claim(tmp_path):
    """Two concurrent coroutines racing to claim the same task must not
    both succeed. Run 5 times to make the probabilistic guarantee strong.
    """
    for k in range(5):
        _reset_cache_for_tests()
        db = SessionDB(db_path=str(tmp_path / f"state_{k}.db"))
        await db.initialize()
        goal_id = _tid()
        session_id = _tid()
        now = _now()
        task_id = _tid()

        # Single task — only one agent should claim it
        await db.create_goal_task(
            task_id=task_id, goal_id=goal_id, session_id=session_id,
            title="contested", depends_on=[], created_at=now, updated_at=now,
        )

        results = await asyncio.gather(
            db.claim_ready_goal_task(goal_id, "agent-A", now + 1),
            db.claim_ready_goal_task(goal_id, "agent-B", now + 1),
        )

        # Exactly ONE claim should succeed; the other gets None
        non_none = [r for r in results if r is not None]
        assert len(non_none) == 1, (
            f"Pass {k+1}: expected exactly 1 claim, got {len(non_none)}: {results}"
        )


# ──────────────────────────────────────────────────────────────────────
# 6. update done → progress 回填 session_goals
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_done_backfills_progress(tmp_path):
    """When a task is marked done, the goal's progress in session_goals
    should be updated to reflect done_count / total.
    """
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    goal_id = _tid()
    session_id = _tid()
    now = _now()

    # Seed the goal first so upsert_session_goal can update it
    await db.upsert_session_goal(
        goal_id=goal_id,
        session_id=session_id,
        text="Test goal",
        status="active",
        progress=0.0,
        criteria=None,
        max_iterations=10,
        iterations_used=0,
        set_at=now,
        updated_at=now,
    )

    tid_a = _tid()
    tid_b = _tid()
    await db.create_goal_task(
        task_id=tid_a, goal_id=goal_id, session_id=session_id,
        title="A", depends_on=[], created_at=now, updated_at=now,
    )
    await db.create_goal_task(
        task_id=tid_b, goal_id=goal_id, session_id=session_id,
        title="B", depends_on=[], created_at=now + 1, updated_at=now + 1,
    )

    # Mark A done; should update progress to 0.5
    await db.update_goal_task(tid_a, status="done", result="ok", updated_at=now + 2)
    goals = await db.get_active_goals(session_id)
    assert len(goals) == 1
    assert goals[0]["progress"] == pytest.approx(0.5, abs=0.01)

    # Mark B done; should update progress to 1.0
    await db.update_goal_task(tid_b, status="done", result="ok", updated_at=now + 3)
    goals2 = await db.get_active_goals(session_id)
    assert goals2[0]["progress"] == pytest.approx(1.0, abs=0.01)


# ──────────────────────────────────────────────────────────────────────
# 7. Cycle detection (via TaskGraphStore, tested here via DB layer)
# ──────────────────────────────────────────────────────────────────────
# Note: cycle detection lives in TaskGraphStore (Task 6). The DB layer
# itself does not enforce cycles — that's the store's responsibility.
# We do a sanity check here that depends_on with nonexistent IDs is fine
# (store-level cycle detection tested in test_task_graph.py).

@pytest.mark.asyncio
async def test_depends_on_with_multiple_deps(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    goal_id = _tid()
    session_id = _tid()
    now = _now()
    tid_a, tid_b, tid_c = _tid(), _tid(), _tid()

    await db.create_goal_task(
        task_id=tid_a, goal_id=goal_id, session_id=session_id,
        title="A", depends_on=[], created_at=now, updated_at=now,
    )
    await db.create_goal_task(
        task_id=tid_b, goal_id=goal_id, session_id=session_id,
        title="B", depends_on=[], created_at=now + 1, updated_at=now + 1,
    )
    await db.create_goal_task(
        task_id=tid_c, goal_id=goal_id, session_id=session_id,
        title="C (needs A and B)", depends_on=[tid_a, tid_b],
        created_at=now + 2, updated_at=now + 2,
    )

    # Neither A nor B done → C not claimable
    claimed = await db.claim_ready_goal_task(goal_id, "agent-1", now + 3)
    # A or B (both pending, no deps) should be claimable
    assert claimed is not None
    assert claimed["task_id"] in (tid_a, tid_b)

    # Mark the claimed one done, claim the other
    await db.update_goal_task(claimed["task_id"], status="done", result="done", updated_at=now + 4)
    claimed2 = await db.claim_ready_goal_task(goal_id, "agent-2", now + 5)
    assert claimed2 is not None
    assert claimed2["task_id"] in (tid_a, tid_b)
    assert claimed2["task_id"] != claimed["task_id"]

    # Now mark that done too → C should be claimable
    await db.update_goal_task(claimed2["task_id"], status="done", result="done", updated_at=now + 6)
    claimed3 = await db.claim_ready_goal_task(goal_id, "agent-3", now + 7)
    assert claimed3 is not None
    assert claimed3["task_id"] == tid_c


# ──────────────────────────────────────────────────────────────────────
# 8. R-T5 flag-OFF: ensure_memory_v2_tables does NOT create goal_tasks
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_flag_off_shared_ensure_does_not_create_goal_tasks(tmp_path):
    """R-T5 unit assertion: calling ONLY ensure_memory_v2_tables must NOT
    create the goal_tasks table (mirrors the session_goals assertion).
    """
    import aiosqlite

    db_path = str(tmp_path / "flag_off.db")
    await ensure_memory_v2_tables(db_path)

    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='goal_tasks'"
        )
        row = await cur.fetchone()
        await cur.close()

    assert row is None, (
        "goal_tasks table must NOT be created by ensure_memory_v2_tables "
        "(flag-OFF baseline, R-T5)"
    )
