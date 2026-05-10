"""glob — find files by glob pattern under the project root.

Pattern syntax follows ``pathlib.PurePath.match`` semantics, plus
``**`` for recursive descent (via ``Path.rglob``):

    "**/*.py"        → all .py files anywhere in the tree
    "src/*.ts"       → only direct children of src/
    "test_*.py"      → top-level test files (no recursion)

Output order is descending mtime — newest matches first — so the LLM
can find recently-touched files without paginating through every match.

Cap: 200 results. The LLM should narrow the pattern if it hits the cap
(returned in the metadata so it can tell).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

_MAX_RESULTS = 200


def _resolve_root(args: dict[str, Any]) -> Path:
    """Pick the search root — caller-supplied path > injected project_root."""
    p = args.get("path") or args.get("_project_root")
    if not p:
        raise ValueError("glob: no path provided and no project_root injected")
    return Path(p).expanduser().resolve()


def glob_tool(args: dict[str, Any], task_id: str = "") -> str:
    """Tool handler. ``args["pattern"]`` is required; ``args["path"]``
    optional — defaults to the injected project root.

    The chat handler injects ``args["_project_root"] = str(project_root)``
    before dispatch so this tool can run without the LLM having to
    re-state the root every call.
    """
    pattern = args.get("pattern")
    if not pattern or not isinstance(pattern, str):
        return json.dumps({"error": "pattern (string) is required"})

    try:
        root = _resolve_root(args)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    if not root.is_dir():
        return json.dumps({"error": f"path is not a directory: {root}"})

    try:
        # ``rglob("**/X")`` and ``glob("**/X", recursive=True)`` differ —
        # rglob with a non-** pattern descends; rglob("**") includes the
        # root itself. We pass the user pattern straight through.
        matches: list[Path] = list(root.rglob(pattern))
    except OSError as e:
        log.warning("glob failed: %s", e)
        return json.dumps({"error": f"OS error during glob: {e}"})

    # Stable mtime sort (descending). Files that vanished between rglob
    # and stat fall back to mtime=0 so they sort last instead of crashing.
    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    matches.sort(key=_mtime, reverse=True)

    truncated = len(matches) > _MAX_RESULTS
    keep = matches[:_MAX_RESULTS]

    out: dict[str, Any] = {
        "pattern": pattern,
        "root": str(root),
        "count": len(keep),
        "total_match": len(matches),
        "truncated": truncated,
        "files": [str(p) for p in keep],
    }
    return json.dumps(out, ensure_ascii=False)
