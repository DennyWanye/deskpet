"""P4-S5: todo tools (toolset=todo).

Simplified flat-JSON storage: a single ``todo.json`` under the user data
directory. S5 spec calls for a proper ``todos`` SQLite table in a later
slice; we keep the JSON form here so the schema is live on day one.
Format:

    {
      "todos": [
        {"id": "<uuid>", "title": "...", "due_date": "ISO or null",
         "priority": "normal|high|low", "status": "open|done",
         "created_at": "ISO", "completed_at": "ISO or null"}
      ]
    }

Concurrency: a process-wide lock serializes read-modify-write so two
near-simultaneous ``todo_write`` calls don't clobber each other.
Cross-process safety is out of scope for S5 (single backend process).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import platformdirs

from .registry import registry

logger = logging.getLogger(__name__)

_APP_NAME = "deskpet"
_VALID_PRIORITIES = {"low", "normal", "high"}

_file_lock = threading.Lock()


def _todo_path() -> Path:
    override = os.environ.get("DESKPET_TODO_PATH")
    if override:
        p = Path(override)
    else:
        base = Path(
            platformdirs.user_data_dir(_APP_NAME, appauthor=False, roaming=True)
        )
        p = base / "todo.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> dict[str, Any]:
    p = _todo_path()
    if not p.exists():
        return {"todos": []}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("todo.json unreadable (%s); starting fresh", exc)
        return {"todos": []}
    if not isinstance(data, dict) or not isinstance(data.get("todos"), list):
        return {"todos": []}
    return data


def _save(data: dict[str, Any]) -> None:
    p = _todo_path()
    tmp = p.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # Atomic replace — even if the backend is killed mid-write we won't
    # corrupt todo.json (tmp is orphaned, caller retries on next write).
    os.replace(tmp, p)


def _err(msg: str, retriable: bool = False) -> str:
    return json.dumps({"error": msg, "retriable": retriable}, ensure_ascii=False)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------
# todo_write
# ---------------------------------------------------------------------
_SCHEMA_WRITE: dict[str, Any] = {
    "name": "todo_write",
    "description": (
        "Create a new todo item. Returns its id. Use 'due_date' in "
        "ISO 8601 (YYYY-MM-DD) or leave null for no deadline."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Todo title (required, non-empty).",
            },
            "due_date": {
                "type": "string",
                "description": "ISO date (YYYY-MM-DD) or null.",
            },
            "priority": {
                "type": "string",
                "enum": ["low", "normal", "high"],
                "default": "normal",
            },
        },
        "required": ["title"],
    },
}


def _handle_todo_write(args: dict[str, Any], task_id: str) -> str:
    title = str(args.get("title", "") or "").strip()
    if not title:
        return _err("title is required", retriable=False)
    due_date = args.get("due_date")
    if due_date is not None and not isinstance(due_date, str):
        return _err("due_date must be string or null", retriable=False)
    priority = str(args.get("priority", "normal") or "normal")
    if priority not in _VALID_PRIORITIES:
        return _err(
            f"priority must be one of {sorted(_VALID_PRIORITIES)}",
            retriable=False,
        )
    todo_id = str(uuid.uuid4())
    with _file_lock:
        data = _load()
        data["todos"].append(
            {
                "id": todo_id,
                "title": title,
                "due_date": due_date,
                "priority": priority,
                "status": "open",
                "created_at": _now_iso(),
                "completed_at": None,
            }
        )
        try:
            _save(data)
        except OSError as exc:
            return _err(f"persist failed: {exc}", retriable=True)
    return json.dumps({"todo_id": todo_id, "ok": True}, ensure_ascii=False)


# ---------------------------------------------------------------------
# todo_complete
# ---------------------------------------------------------------------
_SCHEMA_COMPLETE: dict[str, Any] = {
    "name": "todo_complete",
    "description": "Mark a todo as done by id.",
    "parameters": {
        "type": "object",
        "properties": {
            "todo_id": {
                "type": "string",
                "description": "The id returned by todo_write.",
            }
        },
        "required": ["todo_id"],
    },
}


def _handle_todo_complete(args: dict[str, Any], task_id: str) -> str:
    todo_id = str(args.get("todo_id", "") or "").strip()
    if not todo_id:
        return _err("todo_id is required", retriable=False)
    with _file_lock:
        data = _load()
        found = False
        for t in data["todos"]:
            if t.get("id") == todo_id:
                t["status"] = "done"
                t["completed_at"] = _now_iso()
                found = True
                break
        if not found:
            return _err(f"todo not found: {todo_id}", retriable=False)
        try:
            _save(data)
        except OSError as exc:
            return _err(f"persist failed: {exc}", retriable=True)
    return json.dumps({"ok": True, "todo_id": todo_id})


registry.register("todo_write", "todo", _SCHEMA_WRITE, _handle_todo_write)
registry.register(
    "todo_complete", "todo", _SCHEMA_COMPLETE, _handle_todo_complete
)
