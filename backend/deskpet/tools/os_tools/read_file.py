"""read_file tool — `read_file(path, offset=0, limit=2000)`.

Permission category: ``read_file`` (default-allow). Sensitive paths
(``.ssh/id_rsa``, ``.env``, etc.) are auto-upgraded to
``read_file_sensitive`` by PermissionGate before reaching this handler.

P5-S2 Phase 0: error responses now include ``ok: false`` + ``hint``
+ ``examples``. ``did_you_mean`` fuzzy candidates added when path
ENOENT and the parent directory exists.
"""
from __future__ import annotations

import difflib
import json
import os
from pathlib import Path
from typing import Any


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


def read_file(args: dict[str, Any], task_id: str = "") -> str:
    path = args.get("path", "")
    offset = int(args.get("offset", 0) or 0)
    limit = int(args.get("limit", 2000) or 2000)

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

    return json.dumps(
        {
            "content": content,
            "lines": line_count,
            "truncated": truncated,
        },
        ensure_ascii=False,
    )
