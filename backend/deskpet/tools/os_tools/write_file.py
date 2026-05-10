"""write_file tool — `write_file(path, content, overwrite=False)`.

Permission category: ``write_file``. Creates parent directories
automatically. Refuses to overwrite an existing file unless
``overwrite=True``.

P5-S2 Phase 0: error responses now include ``ok: false`` + ``hint``
+ ``examples``. Legacy ``error`` strings preserved for back-compat.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_EXAMPLES = [
    {"path": "./notes/today.md", "content": "# Today\n- ..."},
    {"path": "main.go", "content": "package main\n"},
    {"path": "existing.txt", "content": "new", "overwrite": True},
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


def write_file(args: dict[str, Any], task_id: str = "") -> str:
    path = args.get("path", "")
    content = args.get("content", "")
    overwrite = bool(args.get("overwrite", False))

    if not isinstance(path, str) or not path:
        return _err(
            "path required",
            "write_file 的 path 字段必填，且必须是非空字符串。"
            "例如 {\"path\": \"./hello.txt\", \"content\": \"hi\"}。",
        )
    if not isinstance(content, str):
        return _err(
            "content must be string",
            "write_file 的 content 必须是字符串。"
            f"当前传入的是 {type(content).__name__}。"
            "如要写二进制请先 base64 编码成字符串。",
            expected="string",
            got=type(content).__name__,
        )

    p = Path(path)
    if p.exists() and not overwrite:
        return _err(
            "FileExistsError",
            f"{path} 已存在。如要覆盖请加 overwrite: true，"
            "或改用 edit_file 做增量修改（更安全，不会丢老内容）。",
            path=path,
            alternatives=["pass overwrite=true", "use edit_file"],
        )

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        p.write_bytes(data)
    except OSError as exc:
        return _err(
            f"OSError: {exc}",
            f"写入 {path} 时操作系统报错。常见原因：路径是已存在的目录、"
            "父目录权限不足、磁盘已满、路径包含非法字符。"
            "请确认 path 是文件路径（非目录）且可写。",
            path=path,
        )

    return json.dumps(
        {"path": str(p.resolve()), "bytes_written": len(data)},
        ensure_ascii=False,
    )
