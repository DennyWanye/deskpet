"""list_directory tool — `list_directory(path, max_entries=100)`.

Permission category: ``read_file`` (default-allow). Returns structured
list of files + subdirectories with size for files.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def list_directory(args: dict[str, Any], task_id: str = "") -> str:
    path = args.get("path", "")
    max_entries = int(args.get("max_entries", 100) or 100)

    if not isinstance(path, str) or not path:
        return json.dumps({"error": "path required"})

    p = Path(path)
    if not p.exists():
        return json.dumps({"error": "FileNotFoundError", "path": path})
    if not p.is_dir():
        return json.dumps({"error": "NotADirectory", "path": path})

    try:
        names = sorted(os.listdir(p))
    except OSError as exc:
        return json.dumps({"error": f"OSError: {exc}", "path": path})

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
