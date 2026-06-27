# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-1.2 / WI-TG-1 — goal_task_* tool handlers.

These tools let the main agent (and teammate subagents) read and write
the shared task graph (``goal_tasks`` table) within a goal session.

Two factories expose the same tool family for the two call sites:

* :func:`build_goal_task_tools` — teammate path. Bound to a fixed
  ``goal_id`` at construction (the teammate already knows its parent
  goal). Appended to the teammate tool set by
  ``deskpet.agent.team.teammate_tools.build_teammate_tools``.
* :func:`build_global_goal_task_tools` — main-agent path (WI-TG-1
  方案A). Main-agent global tool handlers have signature
  ``(args, corr_id)`` with **no** session/goal context, but
  :meth:`TaskGraphStore.create` needs both ``goal_id`` and
  ``session_id``. We resolve them at call time via a ``goal_resolver``
  closure that reads the active :class:`SessionGoal` from the
  ``SessionGoalStore`` (反查方案). No active goal → friendly error
  「请先用 /goal 设定目标」.

Tool names are prefixed ``goal_task_`` to avoid collision with the
existing ``team_task_*`` tools (which work on TeamStore, not TaskGraphStore).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Awaitable, Callable, Optional

from deskpet.agent.task_graph import TaskGraphStore

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Schemas
# ──────────────────────────────────────────────────────────────────────

_SCHEMA_GOAL_TASK_LIST: dict[str, Any] = {
    "name": "goal_task_list",
    "description": (
        "List all tasks in the shared goal task graph for the current goal. "
        "Returns task_id, title, status, depends_on, result for each task. "
        "Only relevant when a `/goal` is active — do NOT call in ordinary chat."
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
        "goal task graph. Valid statuses: 'done', 'failed', 'in_progress'. "
        "Only relevant when a `/goal` is active — do NOT call in ordinary chat."
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

_SCHEMA_GOAL_TASK_CREATE: dict[str, Any] = {
    "name": "goal_task_create",
    "description": (
        "Break the CURRENT active long-term goal into a trackable sub-task. "
        "⛔ ONLY call this when the user has explicitly run `/goal` to set a "
        "long-term goal AND that goal is still active. Do NOT call it to plan "
        "or decompose an ordinary chat request — normal conversation has NO "
        "active goal, and calling it then is a mistake that confuses the user. "
        "If you are not certain a `/goal` is active, do NOT call. Just answer "
        "the user directly instead. Returns the new task_id; with no active "
        "goal it returns an error telling the user to run /goal first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short, actionable description of the task.",
            },
            "depends_on": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional list of task_ids this task depends on "
                    "(must already exist). Creates a DAG edge; a cycle is "
                    "rejected with an error."
                ),
            },
            "note": {
                "type": "string",
                "description": (
                    "Optional free-text note stored as the task's initial "
                    "result/context."
                ),
            },
        },
        "required": ["title"],
    },
}

_SCHEMA_GOAL_TASK_GET: dict[str, Any] = {
    "name": "goal_task_get",
    "description": (
        "Fetch a single task from the shared goal task graph by task_id. "
        "Returns task_id, title, status, depends_on, claimed_by, result. "
        "Only relevant when a `/goal` is active — do NOT call in ordinary chat."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "ID of the task to fetch.",
            },
        },
        "required": ["task_id"],
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


# A goal context resolver returns ``(goal_id, session_id)`` for the
# currently-active goal, or ``None`` when no goal is set. Used by the
# main-agent (global) factory to fill the two required args of
# ``TaskGraphStore.create`` that the ``(args, corr_id)`` handler signature
# cannot otherwise supply.
GoalContextResolver = Callable[[], Optional[tuple[str, str]]]


def _node_to_dict(n: Any) -> dict[str, Any]:
    return {
        "task_id": n.task_id,
        "title": n.title,
        "status": n.status,
        "depends_on": n.depends_on,
        "claimed_by": n.claimed_by,
        "result": n.result,
    }


# ──────────────────────────────────────────────────────────────────────
# Shared handler bodies (goal_id/session_id supplied by the caller)
# ──────────────────────────────────────────────────────────────────────

async def _do_list(store: TaskGraphStore, goal_id: str) -> str:
    try:
        nodes = await store.list(goal_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("goal_task_list failed: %s", exc)
        return _err(f"list failed: {type(exc).__name__}")
    return _ok({"tasks": [_node_to_dict(n) for n in nodes]})


async def _do_update(store: TaskGraphStore, args: dict[str, Any]) -> str:
    task_id = args.get("task_id")
    status = args.get("status")
    result = args.get("result")
    if not isinstance(task_id, str) or not task_id:
        return _err("task_id (string) required")
    if status not in ("done", "failed", "in_progress"):
        return _err("status must be one of: done, failed, in_progress")
    try:
        node = await store.update(task_id, status=status, result=result)
    except Exception as exc:  # noqa: BLE001
        log.warning("goal_task_update failed for %s: %s", task_id, exc)
        return _err(f"update failed: {type(exc).__name__}")
    return _ok({"updated": True, "task_id": node.task_id, "status": node.status})


async def _do_create(
    store: TaskGraphStore,
    goal_id: str,
    session_id: str,
    args: dict[str, Any],
) -> str:
    title = args.get("title")
    if not isinstance(title, str) or not title.strip():
        return _err("title (non-empty string) required")
    depends_on = args.get("depends_on")
    if depends_on is not None:
        if not isinstance(depends_on, list) or not all(
            isinstance(d, str) for d in depends_on
        ):
            return _err("depends_on must be a list of task_id strings")
    note = args.get("note")
    try:
        node = await store.create(
            goal_id=goal_id,
            session_id=session_id,
            title=title.strip(),
            depends_on=depends_on,
        )
    except ValueError as exc:
        # cycle detection — surface the message so the LLM can fix deps
        return _err(f"create rejected: {exc}")
    except Exception as exc:  # noqa: BLE001
        log.warning("goal_task_create failed: %s", exc)
        return _err(f"create failed: {type(exc).__name__}")
    # Optional note → store as initial result without changing status.
    if isinstance(note, str) and note.strip():
        try:
            node = await store.update(
                node.task_id, status=node.status, result=note.strip()
            )
        except Exception as exc:  # noqa: BLE001 — note is best-effort
            log.warning("goal_task_create note attach failed: %s", exc)
    return _ok(
        {
            "created": True,
            "task_id": node.task_id,
            "title": node.title,
            "status": node.status,
            "depends_on": node.depends_on,
        }
    )


async def _do_get(store: TaskGraphStore, args: dict[str, Any]) -> str:
    task_id = args.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return _err("task_id (string) required")
    try:
        node = await store.get(task_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("goal_task_get failed for %s: %s", task_id, exc)
        return _err(f"get failed: {type(exc).__name__}")
    if node is None:
        return _err(f"task not found: {task_id}")
    return _ok({"task": _node_to_dict(node)})


# ──────────────────────────────────────────────────────────────────────
# Factory: teammate path — build tool triples bound to (store, goal_id)
# ──────────────────────────────────────────────────────────────────────

def build_goal_task_tools(
    *,
    task_graph_store: TaskGraphStore,
    goal_id: str,
) -> list[tuple[str, dict[str, Any], GoalTaskHandler]]:
    """Build the teammate goal_task tool ``(name, schema, handler)`` triples.

    Teammates only get **list** + **update** — they create tasks via the
    separate ``team_task_create`` tool, and the goal_task_create tool is a
    main-agent capability (WI-TG-1, see :func:`build_global_goal_task_tools`).
    Kept at 2 tools to preserve the existing teammate tool-set contract
    (byte-BC).
    """

    async def _h_list(args: dict[str, Any], corr_id: str = "") -> str:
        return await _do_list(task_graph_store, goal_id)

    async def _h_update(args: dict[str, Any], corr_id: str = "") -> str:
        return await _do_update(task_graph_store, args)

    return [
        (_SCHEMA_GOAL_TASK_LIST["name"], _SCHEMA_GOAL_TASK_LIST, _h_list),
        (_SCHEMA_GOAL_TASK_UPDATE["name"], _SCHEMA_GOAL_TASK_UPDATE, _h_update),
    ]


# ──────────────────────────────────────────────────────────────────────
# Factory: main-agent path — resolve (goal_id, session_id) at call time
# ──────────────────────────────────────────────────────────────────────

_NO_ACTIVE_GOAL_ERR = "请先用 /goal 设定目标后再管理任务图。"


def build_global_goal_task_tools(
    *,
    task_graph_store: TaskGraphStore,
    goal_resolver: GoalContextResolver,
) -> list[tuple[str, dict[str, Any], GoalTaskHandler]]:
    """WI-TG-1 方案A — build the goal_task tools for the main agent.

    Unlike the teammate factory, the main agent has no fixed ``goal_id``;
    ``goal_resolver()`` is invoked **per call** to read the current active
    goal's ``(goal_id, session_id)`` from the SessionGoalStore. When no
    goal is active it returns ``None`` and every tool replies with a
    friendly "set a goal first" error instead of touching the store.
    """

    def _resolve() -> Optional[tuple[str, str]]:
        try:
            return goal_resolver()
        except Exception as exc:  # noqa: BLE001 — never break dispatch
            log.warning("goal_resolver raised: %s", exc)
            return None

    async def _h_list(args: dict[str, Any], corr_id: str = "") -> str:
        ctx = _resolve()
        if ctx is None:
            return _err(_NO_ACTIVE_GOAL_ERR)
        goal_id, _ = ctx
        return await _do_list(task_graph_store, goal_id)

    async def _h_update(args: dict[str, Any], corr_id: str = "") -> str:
        ctx = _resolve()
        if ctx is None:
            return _err(_NO_ACTIVE_GOAL_ERR)
        return await _do_update(task_graph_store, args)

    async def _h_create(args: dict[str, Any], corr_id: str = "") -> str:
        ctx = _resolve()
        if ctx is None:
            return _err(_NO_ACTIVE_GOAL_ERR)
        goal_id, session_id = ctx
        return await _do_create(task_graph_store, goal_id, session_id, args)

    async def _h_get(args: dict[str, Any], corr_id: str = "") -> str:
        ctx = _resolve()
        if ctx is None:
            return _err(_NO_ACTIVE_GOAL_ERR)
        return await _do_get(task_graph_store, args)

    return [
        (_SCHEMA_GOAL_TASK_LIST["name"], _SCHEMA_GOAL_TASK_LIST, _h_list),
        (_SCHEMA_GOAL_TASK_UPDATE["name"], _SCHEMA_GOAL_TASK_UPDATE, _h_update),
        (_SCHEMA_GOAL_TASK_CREATE["name"], _SCHEMA_GOAL_TASK_CREATE, _h_create),
        (_SCHEMA_GOAL_TASK_GET["name"], _SCHEMA_GOAL_TASK_GET, _h_get),
    ]


__all__ = [
    "GoalTaskHandler",
    "GoalContextResolver",
    "build_goal_task_tools",
    "build_global_goal_task_tools",
    "_SCHEMA_GOAL_TASK_LIST",
    "_SCHEMA_GOAL_TASK_UPDATE",
    "_SCHEMA_GOAL_TASK_CREATE",
    "_SCHEMA_GOAL_TASK_GET",
]
