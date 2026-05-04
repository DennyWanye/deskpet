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
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Any


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
        return json.dumps({"error": "name required"})
    if not isinstance(content, str):
        return json.dumps({"error": "content must be string"})
    # Reject path traversal — name must be a single component
    if any(sep in name for sep in ("/", "\\", "..")):
        return json.dumps({"error": "name must not contain path separators"})

    desktop, plat = _resolve_desktop()
    try:
        desktop.mkdir(parents=True, exist_ok=True)
        target = desktop / name
        target.write_bytes(content.encode("utf-8"))
    except OSError as exc:
        return json.dumps({"error": f"OSError: {exc}"})

    return json.dumps(
        {
            "path": str(target),
            "platform": plat,
            "bytes_written": len(content.encode("utf-8")),
        },
        ensure_ascii=False,
    )
