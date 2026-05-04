"""edit_file tool — exact-string replacement, no regex.

Refuses if ``old_string`` is not unique unless ``replace_all=True``.
Permission category: ``write_file``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def edit_file(args: dict[str, Any], task_id: str = "") -> str:
    path = args.get("path", "")
    old = args.get("old_string", "")
    new = args.get("new_string", "")
    replace_all = bool(args.get("replace_all", False))

    if not isinstance(path, str) or not path:
        return json.dumps({"error": "path required"})
    if not isinstance(old, str) or not old:
        return json.dumps({"error": "old_string required"})
    if not isinstance(new, str):
        return json.dumps({"error": "new_string must be string"})

    p = Path(path)
    if not p.exists() or not p.is_file():
        return json.dumps({"error": "FileNotFoundError", "path": path})

    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        return json.dumps({"error": f"OSError: {exc}", "path": path})

    count = text.count(old)
    if count == 0:
        return json.dumps({"error": f"old_string not found"})
    if count > 1 and not replace_all:
        return json.dumps(
            {
                "error": (
                    f"old_string is not unique ({count} matches); "
                    "use replace_all=true"
                )
            }
        )

    if replace_all:
        new_text = text.replace(old, new)
        replacements = count
    else:
        new_text = text.replace(old, new, 1)
        replacements = 1

    try:
        p.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        return json.dumps({"error": f"OSError: {exc}", "path": path})

    return json.dumps(
        {"replacements": replacements, "path": str(p.resolve())},
        ensure_ascii=False,
    )
