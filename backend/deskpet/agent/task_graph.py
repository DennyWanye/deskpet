# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-1.2 — TaskGraphStore: cross-agent shared task DAG.

The task graph is stored in the ``goal_tasks`` table (FP-2 sidecar, same
flag-OFF moat as ``session_goals``).  All access goes through
:class:`SessionDB` which owns the ``_write_lock`` + ``_with_retry``
serialisation.  Teammate coroutines are same-process asyncio (per T5 spike)
so the asyncio.Lock in SessionDB is sufficient — no BEGIN IMMEDIATE needed.

Key invariants:
- ``create`` enforces DAG acyclicity via DFS over existing graph.
- ``claim_ready`` is atomic: uses SessionDB's ``_write_lock`` internally.
- ``update`` to ``done`` triggers progress backfill on ``session_goals``.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from deskpet.memory.session_db import SessionDB


@dataclass
class TaskNode:
    """In-memory representation of one goal_tasks row."""
    task_id: str
    goal_id: str
    session_id: str
    title: str
    status: str
    depends_on: list[str]
    claimed_by: Optional[str]
    result: Optional[str]
    created_at: float
    updated_at: float

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TaskNode":
        return cls(
            task_id=d["task_id"],
            goal_id=d["goal_id"],
            session_id=d["session_id"],
            title=d["title"],
            status=d["status"],
            depends_on=list(d.get("depends_on") or []),
            claimed_by=d.get("claimed_by"),
            result=d.get("result"),
            created_at=float(d["created_at"]),
            updated_at=float(d["updated_at"]),
        )


def _has_cycle(
    new_id: str,
    new_deps: list[str],
    existing: dict[str, list[str]],
) -> bool:
    """DFS cycle detection.

    ``existing`` maps task_id → depends_on list for all tasks already in
    the graph. We're asking: if we add ``new_id`` with ``new_deps``, does
    a cycle exist?

    A cycle exists iff new_id is reachable from any of new_deps through
    the (existing + new) graph.
    """
    # Build a temporary adjacency: task_id → set of tasks it depends on.
    # A cycle means: starting from new_deps, can we reach new_id?
    graph = dict(existing)
    graph[new_id] = list(new_deps)

    # DFS from each new_dep looking for new_id
    visited: set[str] = set()

    def _dfs(node: str) -> bool:
        if node == new_id:
            return True
        if node in visited:
            return False
        visited.add(node)
        for dep in graph.get(node, []):
            if _dfs(dep):
                return True
        return False

    for dep in new_deps:
        visited.clear()
        if _dfs(dep):
            return True
    return False


class TaskGraphStore:
    """High-level task graph operations on top of :class:`SessionDB`.

    One instance is typically shared across the lifetime of a goal.
    Thread-safety is handled by SessionDB's internal ``_write_lock``
    (asyncio.Lock — same-process model per T5 spike).
    """

    def __init__(self, db: "SessionDB") -> None:
        self._db = db

    async def create(
        self,
        goal_id: str,
        session_id: str,
        title: str,
        depends_on: Optional[list[str]] = None,
        task_id: Optional[str] = None,
    ) -> TaskNode:
        """Create a new task in the graph.

        Raises :class:`ValueError` if adding the task would create a cycle.
        ``task_id`` is auto-generated if not supplied (useful for tests to
        force a specific id for cycle-detection assertions).
        """
        deps = list(depends_on or [])
        tid = task_id or str(uuid.uuid4())

        # Cycle detection — load existing graph first
        existing_rows = await self._db.list_goal_tasks(goal_id)
        existing_graph: dict[str, list[str]] = {
            r["task_id"]: r["depends_on"] for r in existing_rows
        }

        if deps and _has_cycle(tid, deps, existing_graph):
            raise ValueError(
                f"Adding task {tid!r} with depends_on={deps!r} would create a "
                f"cycle in goal {goal_id!r}"
            )

        now = time.time()
        await self._db.create_goal_task(
            task_id=tid,
            goal_id=goal_id,
            session_id=session_id,
            title=title,
            depends_on=deps,
            created_at=now,
            updated_at=now,
        )
        row = await self._db.get_goal_task(tid)
        assert row is not None, "create_goal_task succeeded but get returned None"
        return TaskNode.from_dict(row)

    async def claim_ready(
        self,
        goal_id: str,
        agent_id: str,
        now: Optional[float] = None,
    ) -> Optional[TaskNode]:
        """Atomically claim the first ready pending task.

        Returns the claimed :class:`TaskNode` or None if no task is ready.
        """
        ts = now if now is not None else time.time()
        row = await self._db.claim_ready_goal_task(goal_id, agent_id, ts)
        return TaskNode.from_dict(row) if row is not None else None

    async def update(
        self,
        task_id: str,
        status: str,
        result: Optional[str] = None,
    ) -> TaskNode:
        """Update task status (and optionally result).

        When status='done', SessionDB automatically backfills progress on
        the owning goal's session_goals row.
        """
        now = time.time()
        await self._db.update_goal_task(
            task_id,
            status=status,
            result=result,
            updated_at=now,
        )
        row = await self._db.get_goal_task(task_id)
        assert row is not None
        return TaskNode.from_dict(row)

    async def list(self, goal_id: str) -> list[TaskNode]:
        """Return all tasks for a goal, ordered by created_at."""
        rows = await self._db.list_goal_tasks(goal_id)
        return [TaskNode.from_dict(r) for r in rows]

    async def get(self, task_id: str) -> Optional[TaskNode]:
        """Get a single task by id; None if not found."""
        row = await self._db.get_goal_task(task_id)
        return TaskNode.from_dict(row) if row is not None else None


__all__ = ["TaskNode", "TaskGraphStore"]
