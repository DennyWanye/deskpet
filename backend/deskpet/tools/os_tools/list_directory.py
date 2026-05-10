"""list_directory tool — `list_directory(path, max_entries=100)`.

Permission category: ``read_file`` (default-allow). Returns structured
list of files + subdirectories with size for files.

P5-S2 Phase 0: error responses now include ``ok: false`` + ``hint``
+ ``examples``. Legacy ``error`` strings preserved.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


_EXAMPLES = [
    {"path": "."},
    {"path": "src", "max_entries": 50},
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


def list_directory(args: dict[str, Any], task_id: str = "") -> str:
    path = args.get("path", "")
    max_entries = int(args.get("max_entries", 100) or 100)

    if not isinstance(path, str) or not path:
        return _err(
            "path required",
            "list_directory 的 path 字段必填，必须是要列出的目录路径。"
            "例如 {\"path\": \".\"} 列出当前目录。",
        )

    p = Path(path)
    if not p.exists():
        return _err(
            "FileNotFoundError",
            f"{path} 不存在。请确认路径拼写，或先用上一级目录 list_directory 看看。",
            path=path,
        )
    if not p.is_dir():
        return _err(
            "NotADirectory",
            f"{path} 存在但不是目录。"
            "如果是文件请用 read_file 读内容；如果想列出它所在目录请传父目录路径。",
            path=path,
        )

    try:
        names = sorted(os.listdir(p))
    except OSError as exc:
        return _err(
            f"OSError: {exc}",
            f"读取目录 {path} 时操作系统报错。"
            "常见原因：权限不足、目录被删除。请确认有读权限。",
            path=path,
        )

    truncated = len(names) > max_entries
    names = names[:max_entries]

    entries = []
    for name in names:
        full = p / name
        try:
            if full.is_dir():
                entries.append({"name": name, "type": "dir"})
            else:
                size = full.stat().st_size
                entries.append({"name": name, "type": "file", "size": size})
        except OSError:
            entries.append({"name": name, "type": "unknown"})

    return json.dumps(
        {"entries": entries, "truncated": truncated, "path": str(p.resolve())},
        ensure_ascii=False,
    )
