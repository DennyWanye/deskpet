"""Office-file path authorization — the "防手滑" layer (beta builtin skills).

The office skills (excel / doc / pdf / file-organize / ocr) intentionally
work with **real user files anywhere on disk** — unlike ``file_tools.py``
they are NOT confined to the ``%APPDATA%\\deskpet\\workspace`` sandbox.
DeskPet is a single-user desktop pet; a hard sandbox would block the
core use case ("改我桌面上这份合同").

But the LLM can hallucinate a path, or a prompt-injected web page can
ask it to overwrite ``C:\\Windows\\...``. So instead of a sandbox we use
a lightweight **authorization set**:

* A path becomes *authorized* only when the user explicitly picks it
  through the Tauri file/folder picker (``file_picker.rs`` → IPC →
  :func:`authorize_path`).
* Reading / editing an existing file requires the path to be authorized
  (or under an authorized directory).
* Creating a *new* file may target an authorized directory, the system
  temp dir, or — when no path is given — an auto-named temp file.
* A system-directory **blacklist** is a final backstop: even an
  authorized path under ``C:\\Windows`` / ``Program Files`` is refused.

This is "防手滑级" protection, not a security boundary — consistent with
the project's "no sandbox" stance for a local desktop app.

State note: the authorization set is process-global (the backend is one
process per user session). Tests call :func:`clear_authorizations` to
isolate.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Authorization set
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_authorized: set[str] = set()


def _norm(p: str | os.PathLike[str]) -> Path:
    """Normalize to an absolute, resolved path (collapses ``..``)."""
    return Path(p).expanduser().resolve()


def authorize_path(p: str | os.PathLike[str]) -> str:
    """Mark ``p`` (a file or directory) as user-authorized for this session.

    Called by the IPC bridge after the user picks something in the Tauri
    dialog. Returns the normalized path string.
    """
    norm = str(_norm(p))
    with _lock:
        _authorized.add(norm)
    return norm


def clear_authorizations() -> None:
    """Drop every authorization — test isolation hook."""
    with _lock:
        _authorized.clear()


def list_authorized() -> list[str]:
    with _lock:
        return sorted(_authorized)


def is_authorized(p: str | os.PathLike[str]) -> bool:
    """True when ``p`` equals an authorized path or sits under an
    authorized directory.

    Directory authorization is recursive: authorizing ``D:\\docs`` lets
    the tools touch ``D:\\docs\\sub\\a.docx``. ``..`` traversal cannot
    escape, because we compare *resolved* paths.
    """
    target = _norm(p)
    with _lock:
        snapshot = set(_authorized)
    for a in snapshot:
        ap = Path(a)
        if target == ap:
            return True
        # target under an authorized directory?
        try:
            if target.is_relative_to(ap):
                return True
        except (ValueError, AttributeError):
            # is_relative_to exists on 3.9+; AttributeError guard is paranoia.
            pass
    return False


# ---------------------------------------------------------------------------
# System-directory blacklist (final backstop)
# ---------------------------------------------------------------------------
def _system_roots() -> list[Path]:
    roots: list[Path] = []
    for env in ("SystemRoot", "windir"):
        v = os.environ.get(env)
        if v:
            roots.append(_norm(v))
    for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        v = os.environ.get(env)
        if v:
            roots.append(_norm(v))
    # Hard-coded fallbacks in case the env vars are absent.
    for hard in (r"C:\Windows", r"C:\Program Files", r"C:\Program Files (x86)"):
        roots.append(_norm(hard))
    return roots


def is_system_path(p: str | os.PathLike[str]) -> bool:
    """True when ``p`` is inside a protected Windows system directory."""
    target = _norm(p)
    for root in _system_roots():
        try:
            if target == root or target.is_relative_to(root):
                return True
        except (ValueError, AttributeError):
            pass
    return False


# ---------------------------------------------------------------------------
# Resolution helpers used by the office tools
# ---------------------------------------------------------------------------
class PathError(Exception):
    """Raised when a path fails authorization — handlers turn this into
    a permanent (non-retriable) error JSON telling the model to call the
    file-picker tool first."""


def resolve_for_read(p: str | os.PathLike[str]) -> Optional[Path]:
    """Return the resolved path if it is authorized AND exists, else None.

    A ``None`` return means "ask the user to pick the file" — the handler
    converts it into a clear, non-retriable error.
    """
    if not p:
        return None
    target = _norm(p)
    if not is_authorized(target):
        return None
    if not target.exists():
        return None
    return target


def _temp_dir() -> Path:
    return Path(tempfile.gettempdir())


def auto_temp_path(prefix: str, suffix: str) -> Path:
    """``<temp>/<prefix>-<unix_ts><suffix>`` — a fresh writable path."""
    ts = int(time.time())
    name = f"{prefix}-{ts}{suffix}"
    return _temp_dir() / name


def resolve_for_write(
    p: Optional[str | os.PathLike[str]],
    *,
    default_prefix: str,
    default_suffix: str,
) -> Path:
    """Resolve an output path for a *new* file.

    Rules:
      * ``p`` is None  → auto-named file in the system temp dir.
      * ``p`` given    → its parent directory must be authorized OR be
                          the temp dir; and the path must not fall under
                          a system directory.

    Raises :class:`PathError` when the target is not writable per policy.
    """
    if not p:
        return auto_temp_path(default_prefix, default_suffix)

    target = _norm(p)
    if is_system_path(target):
        raise PathError(
            f"refusing to write into a system directory: {target}"
        )

    parent = target.parent
    temp = _temp_dir()
    in_temp = parent == temp or _is_under(parent, temp)
    if in_temp or is_authorized(parent) or is_authorized(target):
        parent.mkdir(parents=True, exist_ok=True)
        return target

    raise PathError(
        "output directory is not authorized — ask the user to pick a "
        f"destination folder via the file picker first: {parent}"
    )


def _is_under(child: Path, parent: Path) -> bool:
    try:
        return child.is_relative_to(parent)
    except (ValueError, AttributeError):
        return False


__all__ = [
    "PathError",
    "authorize_path",
    "clear_authorizations",
    "list_authorized",
    "is_authorized",
    "is_system_path",
    "resolve_for_read",
    "resolve_for_write",
    "auto_temp_path",
]
