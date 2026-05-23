"""P4-S5: file tools (toolset=file).

Sandboxed file CRUD + glob/grep, restricted to
``%APPDATA%/deskpet/workspace/``. Every path the LLM supplies is
resolved via :func:`_resolve_within_workspace`, which:

  1. interprets relative paths against the workspace root;
  2. refuses absolute paths outside the workspace;
  3. refuses ``..`` traversal that escapes after normalization;
  4. refuses UNC / drive-letter paths on Windows.

Rationale: the agent can hallucinate ``C:/Windows/system.ini`` or
``../../etc/passwd`` — either the model's own invention or a prompt
injection from web content — and the tool layer is the last line of
defence before real disk access.

Workspace path resolution tries, in order:

  1. ``DESKPET_WORKSPACE_DIR`` env (tests, CI).
  2. ``user_data_dir() / "workspace"`` — production: ``%APPDATA%\\deskpet\\workspace\\``.

The directory is lazily created on first access so tests using tmp
paths don't need to pre-mkdir.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import platformdirs

from .registry import registry

logger = logging.getLogger(__name__)

_APP_NAME = "deskpet"


# ---------------------------------------------------------------------
# 记忆系统升级 WI-M1.6 — 工作记忆注入点
# ---------------------------------------------------------------------
# file_write / file_read handler 改成 async 后，可直接 await
# WorkspaceMemoryStore.record_action（registry dispatch 用
# iscoroutinefunction 分流，async handler 直接 await，不进 worker thread
# → 不必 run_coroutine_threadsafe，无死锁）。
#
# 模块级函数没有依赖注入途径 —— main.py 在 workspace_memory flag 开时
# 调 set_workspace_store() 把实例塞进来。flag 关 → 保持 None → handler
# 跳过记录（Strangler-Fig：flag 关时 workspace_state 表不会被建）。
_workspace_store: Any | None = None


def set_workspace_store(store: Any | None) -> None:
    """main.py 在 workspace_memory flag 开时注入 WorkspaceMemoryStore。"""
    global _workspace_store
    _workspace_store = store


async def _record_workspace_action(
    *, session_id: str, path: str, action: str, content: str | None,
) -> None:
    """best-effort 记一次文件动作。store 未注入 / 出错 → 静默跳过，
    绝不影响 file 工具主路径。"""
    store = _workspace_store
    if store is None:
        return
    try:
        await store.record_action(
            session_id=session_id or "default",
            path=path,
            action=action,
            content=content,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("workspace record_action failed: %s", exc)


def _workspace_root() -> Path:
    override = os.environ.get("DESKPET_WORKSPACE_DIR")
    if override:
        root = Path(override).resolve()
    else:
        # Match backend/paths.user_data_dir() without importing the
        # backend flat layout (tools module must be import-safe outside
        # the full backend).
        base = Path(
            platformdirs.user_data_dir(_APP_NAME, appauthor=False, roaming=True)
        )
        root = (base / "workspace").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_within_workspace(path_str: str) -> Path | None:
    """Resolve ``path_str`` against the workspace. Return a Path inside
    the workspace on success, or None if the path escapes / is malformed.

    Keep the resolution conservative: we use ``Path.resolve()`` followed
    by ``relative_to(root)`` — if it raises ``ValueError``, the resolved
    absolute path is NOT a descendant of root, so we reject. This
    handles ``..``, symlinks, and drive-letter escapes uniformly.
    """
    if not isinstance(path_str, str) or not path_str:
        return None

    # Refuse explicit absolute paths up front — even if they happen to
    # point inside the workspace, the agent should never be typing raw
    # absolute disk paths. Catches ``C:\\Windows\\...``, ``/etc/passwd``,
    # UNC ``\\\\server\\share`` etc.
    p = Path(path_str)
    if p.is_absolute() or str(p).startswith(("\\\\", "//")):
        return None

    root = _workspace_root()
    candidate = (root / path_str).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _err(msg: str, retriable: bool = False) -> str:
    return json.dumps({"error": msg, "retriable": retriable}, ensure_ascii=False)


# ---------------------------------------------------------------------
# file_read
# ---------------------------------------------------------------------
_SCHEMA_READ: dict[str, Any] = {
    "name": "file_read",
    "description": (
        "Read a UTF-8 text file from the DeskPet workspace. Supports "
        "line offset + limit for large files. Returns content + lines_read."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Workspace-relative path (e.g. 'notes/todo.md').",
            },
            "offset": {
                "type": "integer",
                "description": "0-based starting line. Default 0.",
                "default": 0,
            },
            "limit": {
                "type": "integer",
                "description": "Max lines to return. Default 2000.",
                "default": 2000,
            },
        },
        "required": ["path"],
    },
}


async def _handle_file_read(args: dict[str, Any], task_id: str) -> str:
    # WI-M1.6: handler 改 async —— 成功读后直接 await record_action 记
    # 工作记忆（registry dispatch 对 async handler 直接 await）。
    target = _resolve_within_workspace(str(args.get("path", "")))
    if target is None:
        return _err("path outside workspace", retriable=False)
    offset = int(args.get("offset", 0) or 0)
    limit = int(args.get("limit", 2000) or 2000)
    if offset < 0 or limit < 0:
        return _err("offset and limit must be non-negative", retriable=False)
    if not target.exists():
        return _err(f"file not found: {args.get('path')}", retriable=False)
    if not target.is_file():
        return _err(f"not a regular file: {args.get('path')}", retriable=False)
    try:
        with target.open("r", encoding="utf-8", errors="replace") as f:
            lines: list[str] = []
            for i, line in enumerate(f):
                if i < offset:
                    continue
                if len(lines) >= limit:
                    break
                lines.append(line)
    except OSError as exc:
        return _err(f"read failed: {exc}", retriable=True)
    rel_path = str(target.relative_to(_workspace_root())).replace("\\", "/")
    content = "".join(lines)
    await _record_workspace_action(
        session_id=task_id, path=rel_path, action="read", content=content,
    )
    return json.dumps(
        {
            "content": content,
            "lines_read": len(lines),
            "path": rel_path,
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------
# file_write
# ---------------------------------------------------------------------
_SCHEMA_WRITE: dict[str, Any] = {
    "name": "file_write",
    "description": (
        "Write UTF-8 text to a workspace file. 'overwrite' replaces the "
        "file; 'append' adds to the end. Parent directories are created "
        "automatically."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Workspace-relative path.",
            },
            "content": {
                "type": "string",
                "description": "UTF-8 content to write.",
            },
            "mode": {
                "type": "string",
                "enum": ["overwrite", "append"],
                "default": "overwrite",
            },
        },
        "required": ["path", "content"],
    },
}


async def _handle_file_write(args: dict[str, Any], task_id: str) -> str:
    # WI-M1.6: handler 改 async —— 成功写后 await record_action 记工作记忆。
    target = _resolve_within_workspace(str(args.get("path", "")))
    if target is None:
        return _err("path outside workspace", retriable=False)
    content = args.get("content", "")
    if not isinstance(content, str):
        return _err("content must be a string", retriable=False)
    mode = str(args.get("mode", "overwrite") or "overwrite")
    if mode not in {"overwrite", "append"}:
        return _err(f"invalid mode: {mode}", retriable=False)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        flag = "a" if mode == "append" else "w"
        with target.open(flag, encoding="utf-8") as f:
            written = f.write(content)
    except OSError as exc:
        return _err(f"write failed: {exc}", retriable=True)
    rel_path = str(target.relative_to(_workspace_root())).replace("\\", "/")
    await _record_workspace_action(
        session_id=task_id, path=rel_path, action="write", content=content,
    )
    return json.dumps(
        {
            "bytes_written": written,
            "path": rel_path,
            "mode": mode,
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------
# file_glob
# ---------------------------------------------------------------------
_SCHEMA_GLOB: dict[str, Any] = {
    "name": "file_glob",
    "description": (
        "List workspace files matching a glob pattern. Uses pathlib rglob "
        "semantics (e.g. '**/*.md' for recursive markdown). Returns "
        "workspace-relative paths."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern, e.g. '*.txt' or 'notes/**/*.md'.",
            },
            "root": {
                "type": "string",
                "description": "Sub-root to glob under. Default '.'.",
                "default": ".",
            },
        },
        "required": ["pattern"],
    },
}


def _handle_file_glob(args: dict[str, Any], task_id: str) -> str:
    pattern = str(args.get("pattern", "") or "")
    if not pattern:
        return _err("pattern is required", retriable=False)
    root_rel = str(args.get("root", ".") or ".")
    root = _resolve_within_workspace(root_rel)
    if root is None:
        return _err("path outside workspace", retriable=False)
    if not root.exists():
        return json.dumps({"matches": [], "count": 0})
    workspace = _workspace_root()
    matches: list[str] = []
    try:
        # Use glob for patterns with ** — rglob is "**/<pattern>" which
        # mangles user intent. Path.glob("**/*.md") is what we want.
        for p in root.glob(pattern):
            try:
                rel = p.resolve().relative_to(workspace)
            except ValueError:
                # Defensive: glob shouldn't escape root, but skip if it does.
                continue
            matches.append(str(rel).replace("\\", "/"))
    except OSError as exc:
        return _err(f"glob failed: {exc}", retriable=True)
    matches.sort()
    return json.dumps({"matches": matches, "count": len(matches)}, ensure_ascii=False)


# ---------------------------------------------------------------------
# file_grep
# ---------------------------------------------------------------------
_SCHEMA_GREP: dict[str, Any] = {
    "name": "file_grep",
    "description": (
        "Search a workspace file line-by-line for a regex pattern. Returns "
        "matching line numbers + text. Useful for quick lookups before "
        "reading larger files."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Python regex pattern.",
            },
            "path": {
                "type": "string",
                "description": "Workspace-relative file path.",
            },
            "max_matches": {
                "type": "integer",
                "description": "Stop after N matches. Default 50.",
                "default": 50,
            },
        },
        "required": ["pattern", "path"],
    },
}


def _handle_file_grep(args: dict[str, Any], task_id: str) -> str:
    pattern = str(args.get("pattern", "") or "")
    if not pattern:
        return _err("pattern is required", retriable=False)
    target = _resolve_within_workspace(str(args.get("path", "")))
    if target is None:
        return _err("path outside workspace", retriable=False)
    max_matches = int(args.get("max_matches", 50) or 50)
    if max_matches <= 0:
        return _err("max_matches must be positive", retriable=False)
    if not target.exists() or not target.is_file():
        return _err(f"file not found: {args.get('path')}", retriable=False)
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return _err(f"invalid regex: {exc}", retriable=False)
    out: list[dict[str, Any]] = []
    try:
        with target.open("r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, start=1):
                if rx.search(line):
                    out.append({"line": i, "text": line.rstrip("\n")})
                    if len(out) >= max_matches:
                        break
    except OSError as exc:
        return _err(f"grep failed: {exc}", retriable=True)
    return json.dumps(
        {"matches": out, "count": len(out)}, ensure_ascii=False
    )


# ---------------------------------------------------------------------
# workspace_recall (记忆系统升级 WI-M1.6)
# ---------------------------------------------------------------------
_SCHEMA_WORKSPACE_RECALL: dict[str, Any] = {
    "name": "workspace_recall",
    "description": (
        "Recall files you've already read or written earlier in THIS "
        "task — search by path or content keyword. Use this before "
        "re-reading a file to check if you already know it. Returns "
        "path / last_action / content_summary for each match."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Path fragment or content keyword to search.",
            },
            "limit": {
                "type": "integer",
                "description": "Max results. Default 10.",
                "default": 10,
            },
        },
        "required": ["query"],
    },
}


async def _handle_workspace_recall(args: dict[str, Any], task_id: str) -> str:
    """WI-M1.6: 查回本 task 改过/读过的文件。store 未注入（flag 关）→
    返回 reason=workspace_memory_disabled。"""
    store = _workspace_store
    if store is None:
        return json.dumps(
            {"matches": [], "count": 0, "reason": "workspace_memory_disabled"},
            ensure_ascii=False,
        )
    query = str(args.get("query", "") or "")
    if not query.strip():
        return _err("query is required", retriable=False)
    limit = int(args.get("limit", 10) or 10)
    try:
        rows = await store.recall(
            query, session_id=task_id or "default", limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        return _err(f"workspace_recall failed: {exc}", retriable=True)
    matches = [
        {
            "path": r.get("path"),
            "last_action": r.get("last_action"),
            "content_summary": r.get("content_summary"),
            "byte_size": r.get("byte_size"),
        }
        for r in rows
    ]
    return json.dumps(
        {"matches": matches, "count": len(matches)}, ensure_ascii=False,
    )


# ---------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------
registry.register("file_read", "file", _SCHEMA_READ, _handle_file_read)
registry.register("file_write", "file", _SCHEMA_WRITE, _handle_file_write)
registry.register("file_glob", "file", _SCHEMA_GLOB, _handle_file_glob)
registry.register("file_grep", "file", _SCHEMA_GREP, _handle_file_grep)
registry.register(
    "workspace_recall", "file",
    _SCHEMA_WORKSPACE_RECALL, _handle_workspace_recall,
)
