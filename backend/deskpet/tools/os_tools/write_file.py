"""write_file tool — `write_file(path, content, overwrite=False)`.

Permission category: ``write_file``. Creates parent directories
automatically. Refuses to overwrite an existing file unless
``overwrite=True``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_file(args: dict[str, Any], task_id: str = "") -> str:
    path = args.get("path", "")
    content = args.get("content", "")
    overwrite = bool(args.get("overwrite", False))

    if not isinstance(path, str) or not path:
        return json.dumps({"error": "path required"})
    if not isinstance(content, str):
        return json.dumps({"error": "content must be string"})

    p = Path(path)
    if p.exists() and not overwrite:
        return json.dumps({"error": "FileExistsError", "path": path})

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        p.write_bytes(data)
    except OSError as exc:
        return json.dumps({"error": f"OSError: {exc}", "path": path})

    return json.dumps(
        {"path": str(p.resolve()), "bytes_written": len(data)},
        ensure_ascii=False,
    )
