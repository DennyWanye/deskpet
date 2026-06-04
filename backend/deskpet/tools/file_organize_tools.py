# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Folder organization tool — file-organize builtin skill.

``file_organize(dir_path, mode, dry_run)`` tidies a folder:

* ``by_type`` — group files into ``images/`` ``documents/`` ``...``
* ``by_date`` — group files into ``YYYY-MM/`` by modification time
* ``dedup``   — find byte-identical duplicate files

Safety contract:

* ``dry_run`` defaults to **True** — the first call always returns a
  *plan* and touches nothing. The model shows the plan to the user and
  only re-calls with ``dry_run=False`` after confirmation.
* ``dir_path`` must be a folder the user picked via ``office_pick_file``
  (kind='dir') — see :mod:`office_paths`.
* Moves never overwrite: a name collision in the target folder gets a
  ``" (2)"`` suffix.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from . import office_paths

log = logging.getLogger(__name__)

# Extension → category folder.
_CATEGORY = {
    "images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".tiff", ".ico"},
    "documents": {".doc", ".docx", ".pdf", ".txt", ".md", ".rtf", ".odt"},
    "spreadsheets": {".xls", ".xlsx", ".csv", ".ods"},
    "presentations": {".ppt", ".pptx", ".odp"},
    "videos": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm"},
    "audio": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"},
    "archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2"},
    "code": {".py", ".js", ".ts", ".java", ".c", ".cpp", ".rs", ".go", ".html", ".css", ".json"},
}


def _category_for(ext: str) -> str:
    ext = ext.lower()
    for cat, exts in _CATEGORY.items():
        if ext in exts:
            return cat
    return "others"


def _unique_target(target: Path) -> Path:
    """Return a non-colliding path under target's parent."""
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    n = 2
    while True:
        cand = target.with_name(f"{stem} ({n}){suffix}")
        if not cand.exists():
            return cand
        n += 1


def _hash_file(p: Path, chunk: int = 65536) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _plan_by_type(files: list[Path], root: Path) -> list[dict[str, Any]]:
    plan = []
    for f in files:
        cat = _category_for(f.suffix)
        plan.append({"file": f.name, "action": "move", "target_dir": cat})
    return plan


def _plan_by_date(files: list[Path], root: Path) -> list[dict[str, Any]]:
    plan = []
    for f in files:
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        bucket = time.strftime("%Y-%m", time.localtime(mtime))
        plan.append({"file": f.name, "action": "move", "target_dir": bucket})
    return plan


def _plan_dedup(files: list[Path], root: Path) -> dict[str, Any]:
    by_hash: dict[str, list[str]] = {}
    for f in files:
        try:
            digest = _hash_file(f)
        except OSError:
            continue
        by_hash.setdefault(digest, []).append(f.name)
    groups = [names for names in by_hash.values() if len(names) > 1]
    return {"duplicate_groups": groups}


def file_organize(
    dir_path: str,
    *,
    mode: str = "by_type",
    dry_run: bool = True,
) -> dict[str, Any]:
    """Organize ``dir_path``. See module docstring.

    Returns the plan (always) and, when ``dry_run`` is False, the count
    of files actually moved.
    """
    resolved = office_paths.resolve_for_read(dir_path)
    if resolved is None or not resolved.is_dir():
        return {
            "ok": False,
            "error": (
                "folder not authorized or not found — call office_pick_file "
                "with kind='dir' so the user can choose the folder"
            ),
            "retriable": False,
        }

    mode = (mode or "by_type").lower()
    if mode not in ("by_type", "by_date", "dedup"):
        return {"ok": False, "error": f"unknown mode: {mode}", "retriable": False}

    # Only top-level files (don't recurse into already-organized subdirs).
    files = [p for p in resolved.iterdir() if p.is_file()]

    if mode == "dedup":
        result = _plan_dedup(files, resolved)
        return {"ok": True, "mode": mode, "dry_run": True, **result,
                "note": "dedup is read-only: review duplicates and delete manually"}

    plan = _plan_by_type(files, resolved) if mode == "by_type" else _plan_by_date(files, resolved)

    if dry_run:
        return {"ok": True, "mode": mode, "dry_run": True, "plan": plan,
                "file_count": len(plan)}

    moved = 0
    for item in plan:
        src = resolved / item["file"]
        if not src.exists():
            continue
        target_dir = resolved / item["target_dir"]
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = _unique_target(target_dir / src.name)
        try:
            shutil.move(str(src), str(dest))
            moved += 1
        except OSError as exc:
            log.warning("file_organize move failed %s: %s", src, exc)

    return {"ok": True, "mode": mode, "dry_run": False, "moved": moved,
            "file_count": len(plan)}


# ---------------------------------------------------------------------------
# Tool registry wiring
# ---------------------------------------------------------------------------
_SCHEMA = {
    "name": "file_organize",
    "description": (
        "Tidy a folder the user has picked. mode='by_type' groups files into "
        "category subfolders, 'by_date' into YYYY-MM folders, 'dedup' lists "
        "byte-identical duplicates. ALWAYS call with dry_run=true first, show "
        "the user the plan, and only call again with dry_run=false after the "
        "user confirms. The folder must first be chosen via office_pick_file "
        "(kind='dir')."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "dir_path": {"type": "string", "description": "Absolute path of the folder."},
            "mode": {
                "type": "string",
                "enum": ["by_type", "by_date", "dedup"],
                "default": "by_type",
            },
            "dry_run": {
                "type": "boolean",
                "default": True,
                "description": "True = return plan only (default). False = actually move files.",
            },
        },
        "required": ["dir_path"],
    },
}


def _handle(args: dict[str, Any], task_id: str = "") -> str:
    # Safe default: dry_run is True unless explicitly set to False.
    dry_run = args.get("dry_run", True)
    if not isinstance(dry_run, bool):
        dry_run = str(dry_run).lower() not in ("false", "0", "no")
    result = file_organize(
        str(args.get("dir_path", "")),
        mode=str(args.get("mode") or "by_type"),
        dry_run=dry_run,
    )
    return json.dumps(result, ensure_ascii=False)


def _register() -> None:
    try:
        from .registry import registry

        registry.register(
            "file_organize",
            "office",
            _SCHEMA,
            _handle,
            permission_category="write_file",
            timeout_seconds=60.0,
            concurrency_safe=False,  # G3: bulk filesystem mutation
        )
    except Exception:  # noqa: BLE001
        pass


_register()

__all__ = ["file_organize"]
