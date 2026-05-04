"""read_file tool — `read_file(path, offset=0, limit=2000)`.

Permission category: ``read_file`` (default-allow). Sensitive paths
(``.ssh/id_rsa``, ``.env``, etc.) are auto-upgraded to
``read_file_sensitive`` by PermissionGate before reaching this handler.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_file(args: dict[str, Any], task_id: str = "") -> str:
    path = args.get("path", "")
    offset = int(args.get("offset", 0) or 0)
    limit = int(args.get("limit", 2000) or 2000)

    if not isinstance(path, str) or not path:
        return json.dumps({"error": "path required"})
    p = Path(path)
    if not p.exists():
        return json.dumps({"error": "FileNotFoundError", "path": path})
    if not p.is_file():
        return json.dumps({"error": "NotAFile", "path": path})

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
        return json.dumps({"error": f"OSError: {exc}", "path": path})

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
