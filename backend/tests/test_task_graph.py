# SPDX-License-Identifier: BUSL-1.1
"""WI-1.2 Task 6 — TaskGraphStore + cycle detection + progress recompute."""
from __future__ import annotations

import time
import uuid

import pytest

from deskpet.memory.memory_v2_schema import _reset_cache_for_tests
from deskpet.memory.session_db import SessionDB
from deskpet.agent.task_graph import TaskGraphStore, TaskNode


@pytest.fixture(autouse=True)
def _reset_schema_cache():
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


def _now() -> float:
    return time.time()


def _tid() -> str:
    return str(uuid.uuid4())


# ──────────────────────────────────────────────────────────────────────
# 1. Basic create / get / list
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_and_get(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    store = TaskGraphStore(db)
    goal_id = _tid()
    session_id = _tid()

    node = await store.create(goal_id=goal_id, session_id=session_id, title="Step 1")
    assert isinstance(node, TaskNode)
    assert node.title == "Step 1"
    assert node.status == "pending"
    assert node.depends_on == []

    fetched = await store.get(node.task_id)
    assert fetched is not None
    assert fetched.task_id == node.task_id
    assert fetched.title == "Step 1"


@pytest.mark.asyncio
async def test_list_returns_all_goal_tasks(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    store = TaskGraphStore(db)
    goal_id = _tid()
    session_id = _tid()

    a = await store.create(goal_id=goal_id, session_id=session_id, title="A")
    b = await store.create(goal_id=goal_id, session_id=session_id, title="B", depends_on=[a.task_id])

    tasks = await store.list(goal_id)
    assert len(tasks) == 2
    ids = {t.task_id for t in tasks}
    assert a.task_id in ids
    assert b.task_id in ids


# ──────────────────────────────────────────────────────────────────────
# 2. Claim
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_claim_returns_ready_task(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    store = TaskGraphStore(db)
    goal_id = _tid()
    session_id = _tid()

    a = await store.create(goal_id=goal_id, session_id=session_id, title="A")
    claimed = await store.claim_ready(goal_id, "agent-1")
    assert claimed is not None
    assert claimed.task_id == a.task_id
    assert claimed.status == "claimed"
    assert claimed.claimed_by == "agent-1"


@pytest.mark.asyncio
async def test_claim_respects_dag(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    store = TaskGraphStore(db)
    goal_id = _tid()
    session_id = _tid()

    a = await store.create(goal_id=goal_id, session_id=session_id, title="A")
    b = await store.create(goal_id=goal_id, session_id=session_id, title="B", depends_on=[a.task_id])

    # A should be claimable; B should not (A not done)
    claimed = await store.claim_ready(goal_id, "agent-1")
    assert claimed.task_id == a.task_id

    # B still not claimable
    claimed2 = await store.claim_ready(goal_id, "agent-2")
    assert claimed2 is None

    # Mark A done → B becomes claimable
    await store.update(a.task_id, "done", result="done A")
    claimed3 = await store.claim_ready(goal_id, "agent-2")
    assert claimed3 is not None
    assert claimed3.task_id == b.task_id


# ──────────────────────────────────────────────────────────────────────
# 3. Update + progress recompute
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_done_recomputes_progress(tmp_path):
    """Marking task done triggers session_goals progress recompute."""
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()

    goal_id = _tid()
    session_id = _tid()
    now = _now()

    # Seed the goal row first
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

    store = TaskGraphStore(db)
    a = await store.create(goal_id=goal_id, session_id=session_id, title="A")
    b = await store.create(goal_id=goal_id, session_id=session_id, title="B")

    await store.update(a.task_id, "done", result="done")
    goals = await db.get_active_goals(session_id)
    assert goals[0]["progress"] == pytest.approx(0.5, abs=0.01)

    await store.update(b.task_id, "done", result="done")
    goals2 = await db.get_active_goals(session_id)
    assert goals2[0]["progress"] == pytest.approx(1.0, abs=0.01)


@pytest.mark.asyncio
async def test_update_failed_status(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    store = TaskGraphStore(db)
    goal_id = _tid()
    session_id = _tid()

    a = await store.create(goal_id=goal_id, session_id=session_id, title="A")
    await store.update(a.task_id, "failed", result="error occurred")

    fetched = await store.get(a.task_id)
    assert fetched.status == "failed"
    assert fetched.result == "error occurred"


# ──────────────────────────────────────────────────────────────────────
# 4. Cycle detection
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cycle_detection_direct(tmp_path):
    """A → B → A should be rejected."""
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    store = TaskGraphStore(db)
    goal_id = _tid()
    session_id = _tid()

    a = await store.create(goal_id=goal_id, session_id=session_id, title="A")
    b = await store.create(goal_id=goal_id, session_id=session_id, title="B", depends_on=[a.task_id])

    with pytest.raises(ValueError, match="[Cc]ycle"):
        # trying to make A depend on B creates A→B→A cycle
        await store.create(
            goal_id=goal_id,
            session_id=session_id,
            title="A2",
            task_id=a.task_id,  # reuse same id to force the cycle attempt
            depends_on=[b.task_id],
        )


@pytest.mark.asyncio
async def test_cycle_detection_indirect(tmp_path):
    """A → B → C → A should be rejected."""
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    store = TaskGraphStore(db)
    goal_id = _tid()
    session_id = _tid()

    a = await store.create(goal_id=goal_id, session_id=session_id, title="A")
    b = await store.create(goal_id=goal_id, session_id=session_id, title="B", depends_on=[a.task_id])
    c = await store.create(goal_id=goal_id, session_id=session_id, title="C", depends_on=[b.task_id])

    # Now trying to make A depend on C creates A→B→C→A
    with pytest.raises(ValueError, match="[Cc]ycle"):
        await store.create(
            goal_id=goal_id,
            session_id=session_id,
            title="A2",
            task_id=a.task_id,
            depends_on=[c.task_id],
        )


@pytest.mark.asyncio
async def test_no_false_cycle_rejection(tmp_path):
    """Diamond DAG A → {B, C} → D should NOT raise ValueError."""
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    store = TaskGraphStore(db)
    goal_id = _tid()
    session_id = _tid()

    a = await store.create(goal_id=goal_id, session_id=session_id, title="A")
    b = await store.create(goal_id=goal_id, session_id=session_id, title="B", depends_on=[a.task_id])
    c = await store.create(goal_id=goal_id, session_id=session_id, title="C", depends_on=[a.task_id])
    # D depends on both B and C — diamond, not a cycle
    d = await store.create(goal_id=goal_id, session_id=session_id, title="D", depends_on=[b.task_id, c.task_id])
    assert d.task_id is not None


# ──────────────────────────────────────────────────────────────────────
# 5. TaskNode dataclass
# ──────────────────────────────────────────────────────────────────────

def test_task_node_fields():
    now = _now()
    node = TaskNode(
        task_id="t1",
        goal_id="g1",
        session_id="s1",
        title="Test",
        status="pending",
        depends_on=[],
        claimed_by=None,
        result=None,
        created_at=now,
        updated_at=now,
    )
    assert node.task_id == "t1"
    assert node.status == "pending"
    assert node.depends_on == []
