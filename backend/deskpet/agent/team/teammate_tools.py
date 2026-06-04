# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-G1 — Teammate tools (Companion+Code v2 Multi-Agent Team).

5 tools exposed to a teammate subagent's LLM via a per-team subset
registry. **Never** globally registered — only :func:`spawn_team`
constructs them and binds them to a specific ``(team_id, teammate_id)``.

Tool list
---------

* ``team_task_create(description)`` — append a new pending task
* ``team_task_claim()`` — atomically grab the next pending task
* ``team_task_update(task_id, status, result=None)`` — mark progress
* ``team_task_list(status=None)`` — snapshot current tasks
* ``team_send_message(to_id, content)`` — write to team mailbox

Each handler returns a JSON-encoded string (the contract every tool in
DeskPet follows — :class:`deskpet.tools.registry.ToolRegistry` callers
get a string back).

**Recursion guard**: the constants :data:`FORBIDDEN_TEAMMATE_TOOLS`
list the parent tools that a teammate's tool set must NEVER contain.
:func:`spawn_team` enforces this when assembling the subset registry —
defence-in-depth on top of the per-tool registration filter.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from deskpet.agent.team.team_store import TeamStore

log = logging.getLogger(__name__)


# Names of tools that must NEVER appear in a teammate's tool set —
# would let a teammate fan out further (mid-loop infinite recursion)
# or escape the team sandbox.
FORBIDDEN_TEAMMATE_TOOLS = frozenset({"agent", "agent_parallel", "spawn_team"})


# Module-level schemas — pure data, safe to share across teams. Handlers
# capture ``(store, team_id, teammate_id)`` so each team gets its own
# closure set.

_SCHEMA_CREATE: dict[str, Any] = {
    "name": "team_task_create",
    "description": (
        "Create a new pending task on the team's shared task list. "
        "Any teammate can pick it up via team_task_claim. Returns the "
        "new task_id."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Free-text task description (what needs doing).",
            },
        },
        "required": ["description"],
    },
}

_SCHEMA_CLAIM: dict[str, Any] = {
    "name": "team_task_claim",
    "description": (
        "Atomically claim the oldest pending task on the team list. "
        "Returns the task object (with task_id, description) or null if "
        "the pool is empty. After claiming you must call team_task_update "
        "with status='done' (or 'failed') when finished."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}

_SCHEMA_UPDATE: dict[str, Any] = {
    "name": "team_task_update",
    "description": (
        "Mark a task's progress. Valid statuses: 'in_progress', 'done', "
        "'failed'. Pass the final result string when marking 'done' so "
        "the coordinator can collect it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "ID of the task to update (from claim).",
            },
            "status": {
                "type": "string",
                "enum": ["in_progress", "done", "failed"],
                "description": "New status.",
            },
            "result": {
                "type": "string",
                "description": "Final output / summary. Required for 'done'.",
            },
        },
        "required": ["task_id", "status"],
    },
}

_SCHEMA_LIST: dict[str, Any] = {
    "name": "team_task_list",
    "description": (
        "List the team's tasks. Pass status='pending'|'claimed'|"
        "'in_progress'|'done'|'failed' to filter, or omit / 'all' for "
        "everything."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "description": "Optional status filter.",
            },
        },
    },
}

_SCHEMA_SEND_MSG: dict[str, Any] = {
    "name": "team_send_message",
    "description": (
        "Send a short message to another teammate (or 'leader') via the "
        "team mailbox. The recipient sees it on their next status poll."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "to_id": {
                "type": "string",
                "description": "Recipient teammate_id (or 'leader').",
            },
            "content": {
                "type": "string",
                "description": "Message body.",
            },
        },
        "required": ["to_id", "content"],
    },
}


def _envelope_ok(payload: dict[str, Any]) -> str:
    payload.setdefault("ok", True)
    return json.dumps(payload, ensure_ascii=False)


def _envelope_err(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def _emit_metric(event: str, detail: dict[str, Any]) -> None:
    """Best-effort metric record — never raises."""
    try:
        from observability.metrics_sink import record  # type: ignore[import-not-found]

        record(event, detail)
    except Exception as exc:  # noqa: BLE001 — metrics must not break dispatch
        log.debug("team metric %s failed: %s", event, exc)


# Handler type accepted by the team subset registry. Mirrors
# :data:`deskpet.tools.registry.ToolHandler` (async variant) — args
# dict + correlation id → JSON string. The dispatch loop awaits.
TeammateHandler = Callable[[dict[str, Any], str], Awaitable[str]]


def build_teammate_tools(
    *,
    store: TeamStore,
    team_id: str,
    teammate_id: str,
) -> list[tuple[str, dict[str, Any], TeammateHandler]]:
    """Build the 5 teammate-tool ``(name, schema, handler)`` triples
    for ``(team_id, teammate_id)``.

    Returns a list (not a dict) so caller registers each into whatever
    subset-registry shape they're using. Names match the ``"name"``
    key in each tool's schema for ease of lookup.
    """

    async def _h_create(args: dict[str, Any], task_id: str = "") -> str:
        desc = args.get("description")
        if not isinstance(desc, str) or not desc.strip():
            return _envelope_err("description (non-empty string) required")
        try:
            new_id = await store.create_task(team_id, desc.strip())
        except Exception as exc:  # noqa: BLE001 — safe-fail per tool contract
            log.warning("team_task_create failed: %s", exc)
            return _envelope_err(f"create_task failed: {type(exc).__name__}")
        _emit_metric(
            "team_task_created",
            {"team_id": team_id, "task_id": new_id, "teammate_id": teammate_id},
        )
        return _envelope_ok({"task_id": new_id})

    async def _h_claim(args: dict[str, Any], task_id: str = "") -> str:
        try:
            task = await store.claim_task(team_id, teammate_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("team_task_claim failed: %s", exc)
            return _envelope_err(f"claim_task failed: {type(exc).__name__}")
        if task is None:
            return _envelope_ok({"task": None})
        _emit_metric(
            "team_task_claimed",
            {"team_id": team_id, "task_id": task.task_id, "teammate_id": teammate_id},
        )
        return _envelope_ok({"task": task.to_dict()})

    async def _h_update(args: dict[str, Any], task_id: str = "") -> str:
        tid = args.get("task_id")
        status = args.get("status")
        result = args.get("result")
        if not isinstance(tid, str) or not tid:
            return _envelope_err("task_id (string) required")
        if status not in ("in_progress", "done", "failed"):
            return _envelope_err(
                "status must be one of: in_progress, done, failed"
            )
        if status == "done" and result is None:
            # Be lenient — accept empty-string result but warn the LLM
            result = ""
        result_str = None if result is None else str(result)
        try:
            ok = await store.update_task(team_id, tid, status, result_str)
        except Exception as exc:  # noqa: BLE001
            log.warning("team_task_update failed: %s", exc)
            return _envelope_err(f"update_task failed: {type(exc).__name__}")
        if not ok:
            return _envelope_err(
                f"task_id {tid!r} not found (or invalid status transition)"
            )
        if status in ("done", "failed"):
            _emit_metric(
                "team_task_done",
                {
                    "team_id": team_id,
                    "task_id": tid,
                    "teammate_id": teammate_id,
                    "status": status,
                },
            )
        return _envelope_ok({"updated": True})

    async def _h_list(args: dict[str, Any], task_id: str = "") -> str:
        status_filter = args.get("status")
        if status_filter is not None and not isinstance(status_filter, str):
            return _envelope_err("status must be a string if provided")
        try:
            tasks = await store.list_tasks(team_id, status_filter)
        except Exception as exc:  # noqa: BLE001
            log.warning("team_task_list failed: %s", exc)
            return _envelope_err(f"list_tasks failed: {type(exc).__name__}")
        return _envelope_ok({"tasks": [t.to_dict() for t in tasks]})

    async def _h_send_msg(args: dict[str, Any], task_id: str = "") -> str:
        to_id = args.get("to_id")
        content = args.get("content")
        if not isinstance(to_id, str) or not to_id:
            return _envelope_err("to_id (string) required")
        if not isinstance(content, str) or not content:
            return _envelope_err("content (non-empty string) required")
        try:
            ok = await store.send_message(team_id, teammate_id, to_id, content)
        except Exception as exc:  # noqa: BLE001
            log.warning("team_send_message failed: %s", exc)
            return _envelope_err(f"send_message failed: {type(exc).__name__}")
        if not ok:
            return _envelope_err("send_message returned False")
        return _envelope_ok({"sent": True})

    return [
        (_SCHEMA_CREATE["name"], _SCHEMA_CREATE, _h_create),
        (_SCHEMA_CLAIM["name"], _SCHEMA_CLAIM, _h_claim),
        (_SCHEMA_UPDATE["name"], _SCHEMA_UPDATE, _h_update),
        (_SCHEMA_LIST["name"], _SCHEMA_LIST, _h_list),
        (_SCHEMA_SEND_MSG["name"], _SCHEMA_SEND_MSG, _h_send_msg),
    ]


__all__ = [
    "FORBIDDEN_TEAMMATE_TOOLS",
    "TeammateHandler",
    "build_teammate_tools",
]
