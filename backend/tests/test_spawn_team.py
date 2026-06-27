# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-G1 — spawn_team orchestrator tests (Companion+Code v2 Multi-Agent Team).

Strategy: inject a fake ``teammate_runner`` that calls claim/update
loops directly on the store — exercises the real :class:`TeamStore`
+ real tool handlers, no AgentLoop / LLM needed.

Coverage:
* 3 teammates + 3 tasks → all done concurrently
* Pool partially fails → integer succeed + failed coexist
* Timeout exits cleanly with timed_out=True
* Empty task_descriptions rejected
* num_teammates out of range rejected
* Charter prompt contains team_id + teammate_id + pool summary
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from deskpet.agent.team.spawn_team import (
    _build_charter,
    _summarise_pool,
    spawn_team,
)
from deskpet.agent.team.team_store import TeamStore


@pytest.fixture
def store(tmp_path: Path) -> TeamStore:
    return TeamStore(tmp_path / "teams")


def _make_claim_loop_runner(*, work_fn=None, fail_first=False):
    """Build a teammate runner that loops claim → update.

    Args:
        work_fn: optional sync callable(task_dict) → str (the result).
        fail_first: if True, the *first* task each teammate claims is
            marked failed (used to test partial-failure resilience).
    """

    async def _runner(
        charter: str,
        teammate_id: str,
        team_tools: list[tuple[str, dict[str, Any], Any]],
    ) -> None:
        # Lookup handlers by name.
        handlers = {n: h for n, _s, h in team_tools}
        h_claim = handlers["team_task_claim"]
        h_update = handlers["team_task_update"]
        seen = 0
        while True:
            raw = await h_claim({}, "")
            payload = json.loads(raw)
            task = payload.get("task")
            if task is None:
                return  # pool empty
            seen += 1
            tid = task["task_id"]
            if fail_first and seen == 1:
                await h_update(
                    {"task_id": tid, "status": "failed", "result": "boom"},
                    "",
                )
                continue
            result = (
                work_fn(task) if work_fn else f"{teammate_id} did {task['description']}"
            )
            await h_update(
                {"task_id": tid, "status": "done", "result": result},
                "",
            )

    return _runner


@pytest.mark.asyncio
async def test_spawn_team_three_teammates_three_tasks_all_done(
    store: TeamStore,
) -> None:
    runner = _make_claim_loop_runner()
    result = await spawn_team(
        team_id="team-alpha",
        task_descriptions=["A", "B", "C"],
        num_teammates=3,
        store=store,
        teammate_runner=runner,
    )
    assert result["ok"] is True
    assert result["team_id"] == "team-alpha"
    assert result["timed_out"] is False
    assert result["elapsed_ms"] >= 0
    statuses = [t["status"] for t in result["results"]]
    assert sorted(statuses) == ["done", "done", "done"]
    # Every task should have a teammate claim
    for t in result["results"]:
        assert t["claimed_by"] is not None
        assert t["result"] is not None
        assert t["done_at"] is not None


@pytest.mark.asyncio
async def test_spawn_team_partial_failure_does_not_crash(
    store: TeamStore,
) -> None:
    runner = _make_claim_loop_runner(fail_first=True)
    result = await spawn_team(
        team_id="team-beta",
        task_descriptions=["A", "B", "C", "D"],
        num_teammates=2,
        store=store,
        teammate_runner=runner,
    )
    assert result["ok"] is True
    statuses = [t["status"] for t in result["results"]]
    # Each teammate fails its *first* claim — but how many "firsts"
    # actually happen depends on scheduling (a teammate might claim,
    # fail, and claim again before the other teammate gets scheduled).
    # The hard guarantee: at least 1 failed (since at least one
    # teammate's first claim must execute), all tasks reach terminal
    # state, no crashes.
    assert statuses.count("failed") >= 1
    assert statuses.count("pending") == 0
    assert statuses.count("claimed") == 0
    assert statuses.count("in_progress") == 0
    total_terminal = statuses.count("done") + statuses.count("failed")
    assert total_terminal == 4
    # Each task must have a recorded result
    for t in result["results"]:
        assert t["result"] is not None


@pytest.mark.asyncio
async def test_spawn_team_more_teammates_than_tasks(store: TeamStore) -> None:
    """5 teammates, 2 tasks → 2 do work, 3 see empty pool and exit."""
    runner = _make_claim_loop_runner()
    result = await spawn_team(
        team_id="team-c",
        task_descriptions=["A", "B"],
        num_teammates=5,
        store=store,
        teammate_runner=runner,
    )
    assert result["ok"] is True
    statuses = [t["status"] for t in result["results"]]
    assert sorted(statuses) == ["done", "done"]


@pytest.mark.asyncio
async def test_spawn_team_empty_task_list_rejected(store: TeamStore) -> None:
    result = await spawn_team(
        team_id="team-x",
        task_descriptions=[],
        num_teammates=2,
        store=store,
        teammate_runner=lambda *_a, **_k: None,
    )
    assert result["ok"] is False
    assert "non-empty" in result["error"]


@pytest.mark.asyncio
async def test_spawn_team_invalid_num_teammates_rejected(store: TeamStore) -> None:
    bad = await spawn_team(
        team_id="t1",
        task_descriptions=["A"],
        num_teammates=0,
        store=store,
        teammate_runner=lambda *_a, **_k: None,
    )
    assert bad["ok"] is False
    too_many = await spawn_team(
        team_id="t1",
        task_descriptions=["A"],
        num_teammates=99,
        store=store,
        teammate_runner=lambda *_a, **_k: None,
    )
    assert too_many["ok"] is False


@pytest.mark.asyncio
async def test_spawn_team_bad_description_type_rejected(store: TeamStore) -> None:
    result = await spawn_team(
        team_id="t1",
        task_descriptions=["valid", "", "also valid"],  # empty string
        num_teammates=2,
        store=store,
        teammate_runner=lambda *_a, **_k: None,
    )
    assert result["ok"] is False
    assert "non-empty string" in result["error"]


@pytest.mark.asyncio
async def test_spawn_team_timeout_returns_timed_out_true(
    store: TeamStore,
) -> None:
    """Hang-forever runner; expect timed_out=True after the wait."""

    async def _hang_runner(*_args: Any, **_kwargs: Any) -> None:
        await asyncio.sleep(60)  # would block forever for test purposes

    result = await spawn_team(
        team_id="team-timeout",
        task_descriptions=["A"],
        num_teammates=1,
        store=store,
        teammate_runner=_hang_runner,
        timeout_seconds=0.3,
    )
    assert result["ok"] is True
    assert result["timed_out"] is True
    # Task should still be pending (nobody ever claimed)
    statuses = [t["status"] for t in result["results"]]
    assert statuses == ["pending"]


def test_build_charter_contains_ids_and_pool() -> None:
    charter = _build_charter(
        team_id="t-42",
        teammate_id="tm-7",
        initial_pool_summary="1. do A\n2. do B",
    )
    assert "t-42" in charter
    assert "tm-7" in charter
    assert "do A" in charter
    assert "team_task_claim" in charter
    assert "team_task_update" in charter


def test_summarise_pool_truncates_long_descriptions() -> None:
    long = "x" * 500
    summary = _summarise_pool([long])
    # Should be truncated to ~200 chars + ellipsis
    assert "..." in summary
    assert len(summary) < 250


def test_summarise_empty_pool() -> None:
    assert _summarise_pool([]) == "(empty)"
