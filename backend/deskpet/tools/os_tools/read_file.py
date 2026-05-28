# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""read_file tool — `read_file(path, offset=0, limit=2000)`.

Permission category: ``read_file`` (default-allow). Sensitive paths
(``.ssh/id_rsa``, ``.env``, etc.) are auto-upgraded to
``read_file_sensitive`` by PermissionGate before reaching this handler.

P5-S2 Phase 0: error responses now include ``ok: false`` + ``hint``
+ ``examples``. ``did_you_mean`` fuzzy candidates added when path
ENOENT and the parent directory exists.

Stage 2 round 2 真测试 bug fix：原本只有 ``file_tools.py`` 的
``file_read`` / ``file_write`` 走 workspace_store hook，但 code mode
agent 实际调的是这里的 ``read_file`` (os_tools 的)。导致
``workspace_state`` 表永远空 → workspace_recall 返回不到任何东西 →
工作记忆失效。Fix：在这里也调 file_tools.set_workspace_store 注入的
同一个 store，hook 同样的 record_action。
"""
from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_EXAMPLES = [
    {"path": "README.md"},
    {"path": "src/main.py", "offset": 0, "limit": 200},
]


def _err(error: str, hint: str, **extra: Any) -> str:
    body: dict[str, Any] = {
        "ok": False,
        "error": error,
        "hint": hint,
        "examples": _EXAMPLES,
    }
    body.update(extra)
    return json.dumps(body, ensure_ascii=False)


def _did_you_mean(path: Path, max_n: int = 5) -> list[str]:
    """Return up to max_n fuzzy-match siblings for a missing path."""
    parent = path.parent
    if not parent.exists() or not parent.is_dir():
        return []
    try:
        siblings = os.listdir(parent)
    except OSError:
        return []
    matches = difflib.get_close_matches(path.name, siblings, n=max_n, cutoff=0.5)
    return [str(parent / m) for m in matches]


def _notify_workspace(*, session_id: str, path: str, content: str | None) -> None:
    """Stage 2 round 2 真测试 fix：os_tools.read_file 也通知
    workspace_store（与 file_tools.file_read 同源）。

    handler 是 sync 函数 (registry 用 run_in_executor 跑 sync handler，
    线程里没 running loop)。用 file_tools 在 set_workspace_store 时
    保存的主 loop reference + run_coroutine_threadsafe 派回主 loop。
    无 store / 无 loop / store error → 静默跳过。
    """
    try:
        from deskpet.tools import file_tools as _ft  # type: ignore
    except Exception:
        return
    store = getattr(_ft, "_workspace_store", None)
    loop = getattr(_ft, "_workspace_loop", None)
    if store is None or loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(
            store.record_action(
                session_id=session_id or "default",
                path=path,
                action="read",
                content=content,
            ),
            loop,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("os_tools.read_file workspace notify failed: %s", exc)


def read_file(args: dict[str, Any], task_id: str = "") -> str:
    path = args.get("path", "")
    offset = int(args.get("offset", 0) or 0)
    limit = int(args.get("limit", 2000) or 2000)
    session_id = (
        args.get("_session_id")
        or args.get("session_id")
        or "default"
    )

    if not isinstance(path, str) or not path:
        return _err(
            "path required",
            "read_file 的 path 字段必填，必须是要读取文件的路径字符串。"
            "例如 {\"path\": \"README.md\"}。可选 offset / limit 控制读取行范围。",
        )
    p = Path(path)
    if not p.exists():
        suggestions = _did_you_mean(p)
        hint = f"{path} 不存在。"
        if suggestions:
            hint += f"也许你想读 {suggestions[0]} ?（共 {len(suggestions)} 个相近路径）"
        else:
            hint += "请先用 list_directory 确认目录里有哪些文件。"
        return _err(
            "FileNotFoundError",
            hint,
            path=path,
            did_you_mean=suggestions,
        )
    if not p.is_file():
        return _err(
            "NotAFile",
            f"{path} 存在但不是文件（可能是目录）。"
            "如果想看目录内容请用 list_directory；如果想读符号链接目标请先 resolve。",
            path=path,
        )

    try:
        # Read line-by-line so offset/limit work on text files.
        # Binary files read as latin-1 to avoid decode errors; first
        # 200 chars are returned with a binary marker.
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            data = p.read_bytes()[:200]
            return json.dumps(
                {
                    "content": data.decode("latin-1", errors="replace"),
                    "lines": 0,
                    "truncated": True,
                    "binary": True,
                }
            )
    except OSError as exc:
        return _err(
            f"OSError: {exc}",
            f"读取 {path} 时操作系统报错。常见原因：权限不足、"
            "文件被占用、I/O 错误。请确认文件可读。",
            path=path,
        )

    lines = text.splitlines()
    truncated = False
    if offset > 0 or limit < len(lines):
        end = offset + limit
        sub = lines[offset:end]
        truncated = end < len(lines)
        content = "\n".join(sub)
        line_count = len(sub)
    else:
        content = text
        line_count = len(lines)

    # Stage 2 round 2 fix：通知 workspace_store（content_summary 由
    # WorkspaceMemoryStore 自己截断）。
    _notify_workspace(
        session_id=str(session_id), path=path, content=content,
    )
    return json.dumps(
        {
            "content": content,
            "lines": line_count,
            "truncated": truncated,
        },
        ensure_ascii=False,
    )
