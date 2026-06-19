# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-1.2 — goal_task_* tool handlers for teammate subagents.

These tools let subagents read and write the shared task graph
(goal_tasks table) within a goal session.  They are NOT globally
registered — :func:`deskpet.agent.team.teammate_tools.build_teammate_tools`
appends them to the teammate tool set only when a :class:`TaskGraphStore`
and ``goal_id`` are supplied.

Tool names are prefixed ``goal_task_`` to avoid collision with the
existing ``team_task_*`` tools (which work on TeamStore, not TaskGraphStore).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Awaitable, Callable

from deskpet.agent.task_graph import TaskGraphStore

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Schemas
# ──────────────────────────────────────────────────────────────────────

_SCHEMA_GOAL_TASK_LIST: dict[str, Any] = {
    "name": "goal_task_list",
    "description": (
        "List all tasks in the shared goal task graph for the current goal. "
        "Returns task_id, title, status, depends_on, result for each task."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}

_SCHEMA_GOAL_TASK_UPDATE: dict[str, Any] = {
    "name": "goal_task_update",
    "description": (
        "Update the status (and optionally result) of a task in the shared "
        "goal task graph. Valid statuses: 'done', 'failed', 'in_progress'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "ID of the task to update.",
            },
            "status": {
                "type": "string",
                "enum": ["done", "failed", "in_progress"],
                "description": "New status.",
            },
            "result": {
                "type": "string",
                "description": "Final output/summary. Include '[off-goal]' if task was not aligned with the parent goal.",
            },
        },
        "required": ["task_id", "status"],
    },
}


# ──────────────────────────────────────────────────────────────────────
# Handler type
# ──────────────────────────────────────────────────────────────────────

GoalTaskHandler = Callable[[dict[str, Any], str], Awaitable[str]]


def _ok(payload: dict[str, Any]) -> str:
    payload.setdefault("ok", True)
    return json.dumps(payload, ensure_ascii=False)


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


# ──────────────────────────────────────────────────────────────────────
# Factory: build tool triples bound to (task_graph_store, goal_id)
# ──────────────────────────────────────────────────────────────────────

def build_goal_task_tools(
    *,
    task_graph_store: TaskGraphStore,
    goal_id: str,
) -> list[tuple[str, dict[str, Any], GoalTaskHandler]]:
    """Build the 2 goal_task tool ``(name, schema, handler)`` triples."""

    async def _h_list(args: dict[str, Any], corr_id: str = "") -> str:
        try:
            nodes = await task_graph_store.list(goal_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("goal_task_list failed: %s", exc)
            return _err(f"list failed: {type(exc).__name__}")
        tasks = [
            {
                "task_id": n.task_id,
                "title": n.title,
                "status": n.status,
                "depends_on": n.depends_on,
                "claimed_by": n.claimed_by,
                "result": n.result,
            }
            for n in nodes
        ]
        return _ok({"tasks": tasks})

    async def _h_update(args: dict[str, Any], corr_id: str = "") -> str:
        task_id = args.get("task_id")
        status = args.get("status")
        result = args.get("result")
        if not isinstance(task_id, str) or not task_id:
            return _err("task_id (string) required")
        if status not in ("done", "failed", "in_progress"):
            return _err("status must be one of: done, failed, in_progress")
        try:
            node = await task_graph_store.update(
                task_id, status=status, result=result
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("goal_task_update failed for %s: %s", task_id, exc)
            return _err(f"update failed: {type(exc).__name__}")
        return _ok({"updated": True, "task_id": node.task_id, "status": node.status})

    return [
        (_SCHEMA_GOAL_TASK_LIST["name"], _SCHEMA_GOAL_TASK_LIST, _h_list),
        (_SCHEMA_GOAL_TASK_UPDATE["name"], _SCHEMA_GOAL_TASK_UPDATE, _h_update),
    ]


__all__ = [
    "GoalTaskHandler",
    "build_goal_task_tools",
    "_SCHEMA_GOAL_TASK_LIST",
    "_SCHEMA_GOAL_TASK_UPDATE",
]
