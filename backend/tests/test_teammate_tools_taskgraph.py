# SPDX-License-Identifier: BUSL-1.1
"""WI-1.2 Task 6 — build_teammate_tools task_graph integration tests.

Tests:
1. With task_graph_store + goal_id → returned tuple list includes TaskList & TaskUpdate
2. Without task_graph_store / goal_id → BC (original 5-tuple count unchanged)
"""
from __future__ import annotations

import time
import uuid

import pytest

from deskpet.memory.memory_v2_schema import _reset_cache_for_tests
from deskpet.memory.session_db import SessionDB
from deskpet.agent.task_graph import TaskGraphStore
from deskpet.agent.team.team_store import TeamStore
from deskpet.agent.team.teammate_tools import build_teammate_tools


@pytest.fixture(autouse=True)
def _reset_schema_cache():
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


def _make_team_store(tmp_path, subdir="team"):
    return TeamStore(base_dir=str(tmp_path / subdir))


# ──────────────────────────────────────────────────────────────────────
# 1. BC: without task_graph_store → original 5-tool set
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_build_teammate_tools_bc_no_task_graph(tmp_path):
    """Without task_graph_store, result must be exactly 5 triples (BC)."""
    store = _make_team_store(tmp_path)
    triples = build_teammate_tools(
        store=store,
        team_id="t1",
        teammate_id="tm1",
    )
    assert len(triples) == 5
    names = {t[0] for t in triples}
    assert names == {
        "team_task_create",
        "team_task_claim",
        "team_task_update",
        "team_task_list",
        "team_send_message",
    }


@pytest.mark.asyncio
async def test_build_teammate_tools_bc_goal_id_none(tmp_path):
    """task_graph_store provided but goal_id=None → BC (no extra tools)."""
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    tgs = TaskGraphStore(db)
    store = _make_team_store(tmp_path)

    triples = build_teammate_tools(
        store=store,
        team_id="t1",
        teammate_id="tm1",
        task_graph_store=tgs,
        goal_id=None,
    )
    assert len(triples) == 5


@pytest.mark.asyncio
async def test_build_teammate_tools_bc_tgs_none(tmp_path):
    """goal_id provided but task_graph_store=None → BC (no extra tools)."""
    store = _make_team_store(tmp_path)

    triples = build_teammate_tools(
        store=store,
        team_id="t1",
        teammate_id="tm1",
        task_graph_store=None,
        goal_id="some-goal-id",
    )
    assert len(triples) == 5


# ──────────────────────────────────────────────────────────────────────
# 2. With task_graph_store + goal_id → includes TaskList + TaskUpdate
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_build_teammate_tools_with_task_graph_includes_extra(tmp_path):
    """With both task_graph_store and goal_id, result includes 7 triples:
    original 5 + goal_task_list + goal_task_update.
    """
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    tgs = TaskGraphStore(db)
    store = _make_team_store(tmp_path)
    goal_id = str(uuid.uuid4())

    triples = build_teammate_tools(
        store=store,
        team_id="t1",
        teammate_id="tm1",
        task_graph_store=tgs,
        goal_id=goal_id,
    )
    assert len(triples) == 7
    names = {t[0] for t in triples}
    assert "goal_task_list" in names
    assert "goal_task_update" in names


# ──────────────────────────────────────────────────────────────────────
# 3. goal_task_list handler calls the real store
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_goal_task_list_handler_returns_tasks(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    tgs = TaskGraphStore(db)
    store = _make_team_store(tmp_path)
    goal_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    # Create a task in the graph store
    node = await tgs.create(goal_id=goal_id, session_id=session_id, title="My task")

    triples = build_teammate_tools(
        store=store,
        team_id="t1",
        teammate_id="tm1",
        task_graph_store=tgs,
        goal_id=goal_id,
    )
    handlers = {t[0]: t[2] for t in triples}

    import json
    result_json = await handlers["goal_task_list"]({}, "corr-1")
    result = json.loads(result_json)
    assert result["ok"] is True
    tasks = result["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["task_id"] == node.task_id


# ──────────────────────────────────────────────────────────────────────
# 4. goal_task_update handler updates the task
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_goal_task_update_handler_marks_done(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    tgs = TaskGraphStore(db)
    store = _make_team_store(tmp_path)
    goal_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    node = await tgs.create(goal_id=goal_id, session_id=session_id, title="Work item")

    triples = build_teammate_tools(
        store=store,
        team_id="t1",
        teammate_id="tm1",
        task_graph_store=tgs,
        goal_id=goal_id,
    )
    handlers = {t[0]: t[2] for t in triples}

    import json
    result_json = await handlers["goal_task_update"](
        {"task_id": node.task_id, "status": "done", "result": "Finished!"}, "corr-2"
    )
    result = json.loads(result_json)
    assert result["ok"] is True

    updated = await tgs.get(node.task_id)
    assert updated.status == "done"
    assert updated.result == "Finished!"
