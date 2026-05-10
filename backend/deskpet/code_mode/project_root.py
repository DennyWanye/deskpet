"""Resolve the on-disk project root for a Code mode session.

Two paths:
1. User explicitly picked a folder via Tauri's directory picker — we
   trust it (after expanduser + resolve), create it if missing.
2. User entered Code mode without a path → we auto-create
   ``%AppData%/deskpet/projects/<sanitized-name>/`` and seed a README so
   subsequent file ops have something concrete to anchor on.

Path safety: callers **must** pass any LLM-tool-supplied paths through
``assert_within_project_root`` to prevent ``../../etc/passwd``-style
escapes.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import paths as _paths  # type: ignore[import-not-found]

log = logging.getLogger(__name__)

# Conservative whitelist: ASCII letters/digits, CJK, dash, underscore,
# space. Strip everything else; drop leading/trailing whitespace.
_NAME_OK_CHARS = re.compile(r"[^\w\s一-鿿-]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")
_MAX_NAME_LEN = 60


def sanitize_project_name(name: str) -> str:
    """Strip OS-illegal chars / collapse whitespace / cap length.

    Returns an empty string when the input cleans to nothing — caller
    should fall back to ``"untitled"``.
    """
    if not name:
        return ""
    cleaned = _NAME_OK_CHARS.sub("", name)
    cleaned = _WHITESPACE_RE.sub("-", cleaned).strip("-_ ")
    if len(cleaned) > _MAX_NAME_LEN:
        cleaned = cleaned[:_MAX_NAME_LEN].rstrip("-_ ")
    return cleaned


def resolve_project_root(
    user_choice: Optional[str],
    llm_suggested_name: str = "untitled",
) -> Path:
    """Materialise a usable project root directory.

    * If ``user_choice`` is provided and non-empty, expand+resolve it,
      ensure it exists, and return it. The directory may already have
      files; we don't touch existing contents.

    * If ``user_choice`` is missing/empty, build
      ``user_data_dir / "projects" / sanitize(llm_suggested_name) or "untitled"``
      and seed a single ``README.md`` so file ops can immediately read+edit
      something. Repeated calls with the same suggested name return the
      same directory (no clobber).
    """
    if user_choice:
        p = Path(user_choice).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    safe_name = sanitize_project_name(llm_suggested_name) or "untitled"
    base = _paths.user_data_dir() / "projects"
    p = (base / safe_name).resolve()
    p.mkdir(parents=True, exist_ok=True)
    readme = p / "README.md"
    if not readme.exists():
        try:
            readme.write_text(
                f"# {safe_name}\n\n"
                "Created by DeskPet Code mode. Edit / delete this file freely.\n",
                encoding="utf-8",
            )
        except OSError as e:
            log.warning("readme_seed_failed: %s", e)
    return p


def assert_within_project_root(candidate: Path, project_root: Path) -> Path:
    """Reject paths that escape the project root.

    Resolves ``candidate`` (handling ``..`` segments) and compares against
    ``project_root.resolve()``. Returns the resolved candidate path on
    success; raises ``ValueError`` otherwise.

    Example:
        assert_within_project_root(Path("../../etc/passwd"), Path("/proj"))
        → ValueError("path escapes project root")
    """
    candidate_abs = candidate.expanduser().resolve()
    root_abs = project_root.resolve()
    try:
        candidate_abs.relative_to(root_abs)
    except ValueError as e:
        raise ValueError(
            f"path escapes project root: {candidate_abs} not under {root_abs}"
        ) from e
    return candidate_abs
