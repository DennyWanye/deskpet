"""desktop_create_file tool — ergonomic wrapper around write_file.

`desktop_create_file(name, content)` resolves to the user's desktop on
Windows / macOS / Linux and writes the file.

Permission category: ``desktop_write``.

Cross-platform desktop resolution:
  - Windows: ``%USERPROFILE%\\Desktop``
  - macOS:   ``$HOME/Desktop``
  - Linux:   ``$HOME/Desktop`` first, fallback to ``xdg-user-dir DESKTOP``

The tool always overwrites — that's the point of the wrapper. Users who
want overwrite protection should call ``write_file`` directly.

P5-S2 Phase 0: error responses now include ``ok: false`` + ``hint``
+ ``examples``. Legacy ``error`` strings preserved.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Any


_EXAMPLES = [
    {"name": "todo.txt", "content": "买牛奶\n回邮件"},
    {"name": "snippet.py", "content": "print('hi')"},
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


def _resolve_desktop() -> tuple[Path, str]:
    """Return (desktop_path, platform_id)."""
    sysname = platform.system()
    if sysname == "Windows":
        base = os.environ.get("USERPROFILE") or os.path.expanduser("~")
        return Path(base) / "Desktop", "windows"
    if sysname == "Darwin":
        return Path(os.path.expanduser("~/Desktop")), "macos"
    # Linux / other Unix
    home_desktop = Path(os.path.expanduser("~/Desktop"))
    if home_desktop.exists():
        return home_desktop, "linux"
    try:
        out = subprocess.run(
            ["xdg-user-dir", "DESKTOP"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip()), "linux"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return home_desktop, "linux"


def desktop_create_file(args: dict[str, Any], task_id: str = "") -> str:
    name = args.get("name", "")
    content = args.get("content", "")

    if not isinstance(name, str) or not name:
        return _err(
            "name required",
            "desktop_create_file 的 name 字段必填，是要在桌面上创建的文件名"
            "（不能含路径分隔符）。例如 {\"name\": \"todo.txt\", \"content\": \"...\"}。",
        )
    if not isinstance(content, str):
        return _err(
            "content must be string",
            "desktop_create_file 的 content 必须是字符串。"
            f"当前传入的是 {type(content).__name__}。",
            expected="string",
            got=type(content).__name__,
        )
    # Reject path traversal — name must be a single component
    if any(sep in name for sep in ("/", "\\", "..")):
        return _err(
            "name must not contain path separators",
            f"name = {name!r} 含路径分隔符或 ..，"
            "桌面只能放单层文件名。如果要写到子目录请用 write_file 给完整路径。",
            alternatives=["use write_file with full path"],
        )

    desktop, plat = _resolve_desktop()
    try:
        desktop.mkdir(parents=True, exist_ok=True)
        target = desktop / name
        target.write_bytes(content.encode("utf-8"))
    except OSError as exc:
        return _err(
            f"OSError: {exc}",
            f"创建桌面文件时操作系统报错。"
            "常见原因：桌面目录权限不足、磁盘已满、文件名含非法字符。",
        )

    return json.dumps(
        {
            "path": str(target),
            "platform": plat,
            "bytes_written": len(content.encode("utf-8")),
        },
        ensure_ascii=False,
    )
