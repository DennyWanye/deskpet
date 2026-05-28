# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""grep — content search across project files via Python ``re``.

We deliberately don't shell out to ripgrep — keeps the frozen bundle
binary-free + cross-platform identical. For typical project sizes
(< 100k lines) Python regex over read_text is plenty fast (sub-second).

Three output modes (matches the Claude Code shape so prompts transfer):

  * ``files_with_matches`` — just paths (the default)
  * ``content`` — paths + line numbers + matching lines
  * ``count`` — paths + total match count per file

Filters:
  * ``glob`` — restrict to files matching this glob (e.g. ``"*.py"``)
  * ``case_insensitive`` — pass IGNORECASE to ``re.compile``
  * ``multiline`` — pass DOTALL + MULTILINE so ``.`` crosses newlines
  * ``context`` — N lines before/after each match (only for ``content``)

Caps: 100 files scanned, 250 result lines emitted. Truncation flag
returned in the JSON so the LLM can re-query with a tighter scope.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_MAX_FILES = 100
_MAX_RESULT_LINES = 250
# Files larger than this get skipped entirely — LLMs grepping
# a 50 MB compiled binary is never useful and would block the loop.
_MAX_FILE_BYTES = 5_000_000


def _iter_files(root: Path, file_glob: str | None) -> list[Path]:
    """Yield candidate files under root, optionally filtered by glob."""
    if file_glob:
        candidates = list(root.rglob(file_glob))
    else:
        candidates = list(root.rglob("*"))
    files: list[Path] = []
    for p in candidates:
        if not p.is_file():
            continue
        try:
            if p.stat().st_size > _MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        files.append(p)
    # Sort by mtime descending so newer hits surface first.
    files.sort(
        key=lambda f: f.stat().st_mtime if f.exists() else 0,
        reverse=True,
    )
    return files


def _safe_read(p: Path) -> str | None:
    """Read text best-effort; return None for binary / unreadable files."""
    try:
        return p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def grep_tool(args: dict[str, Any], task_id: str = "") -> str:
    pattern = args.get("pattern")
    if not pattern or not isinstance(pattern, str):
        return json.dumps({"error": "pattern (regex) is required"})

    path = args.get("path") or args.get("_project_root")
    if not path:
        return json.dumps({"error": "no path provided and no project_root injected"})
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        return json.dumps({"error": f"path is not a directory: {root}"})

    file_glob = args.get("glob")
    if file_glob is not None and not isinstance(file_glob, str):
        return json.dumps({"error": "glob must be string"})

    output_mode = args.get("output_mode", "files_with_matches")
    if output_mode not in {"files_with_matches", "content", "count"}:
        output_mode = "files_with_matches"

    flags = 0
    if args.get("case_insensitive"):
        flags |= re.IGNORECASE
    if args.get("multiline"):
        flags |= re.DOTALL | re.MULTILINE

    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        return json.dumps({"error": f"invalid regex: {e}"})

    context = max(0, int(args.get("context") or 0))

    files = _iter_files(root, file_glob)
    truncated_files = len(files) > _MAX_FILES
    files = files[:_MAX_FILES]

    matches_by_file: dict[str, list[tuple[int, str]]] = {}
    counts: dict[str, int] = {}

    total_lines = 0
    truncated_lines = False
    for f in files:
        text = _safe_read(f)
        if text is None:
            continue
        # Multiline mode: search across the whole text, then map matches
        # back to line numbers. Otherwise: per-line search (fast path).
        if args.get("multiline"):
            file_matches: list[tuple[int, str]] = []
            for m in regex.finditer(text):
                line_no = text.count("\n", 0, m.start()) + 1
                # Snippet: first line of the match
                snippet = m.group(0).split("\n", 1)[0]
                file_matches.append((line_no, snippet))
            cnt = len(file_matches)
        else:
            file_matches = []
            for line_no, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    file_matches.append((line_no, line))
            cnt = len(file_matches)
        if cnt == 0:
            continue
        counts[str(f)] = cnt
        if output_mode == "content":
            # With context: capture surrounding lines per match.
            if context > 0:
                lines = text.splitlines()
                expanded: list[tuple[int, str]] = []
                for line_no, _line in file_matches:
                    start = max(0, line_no - 1 - context)
                    end = min(len(lines), line_no + context)
                    for off in range(start, end):
                        expanded.append((off + 1, lines[off]))
                # Dedupe consecutive duplicate lines (overlapping ctx)
                seen = set()
                dedup: list[tuple[int, str]] = []
                for ln, ltxt in expanded:
                    if ln in seen:
                        continue
                    seen.add(ln)
                    dedup.append((ln, ltxt))
                file_matches = dedup
            matches_by_file[str(f)] = file_matches
            total_lines += len(file_matches)
            if total_lines >= _MAX_RESULT_LINES:
                truncated_lines = True
                break

    out: dict[str, Any] = {
        "pattern": pattern,
        "root": str(root),
        "files_scanned": len(files),
        "files_with_matches": len(counts),
        "truncated_files": truncated_files,
        "truncated_lines": truncated_lines,
    }

    if output_mode == "files_with_matches":
        out["files"] = sorted(counts.keys())
    elif output_mode == "count":
        out["counts"] = counts
    else:  # content
        out["matches"] = {
            f: [{"line": ln, "text": txt} for (ln, txt) in arr]
            for f, arr in matches_by_file.items()
        }

    return json.dumps(out, ensure_ascii=False)
