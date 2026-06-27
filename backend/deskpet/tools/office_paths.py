# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

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

import hashlib
import os
import re
import tempfile
import threading
import time
import unicodedata
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


def _default_output_path(prefix: str, suffix: str, kind: str) -> Path:
    """No path given → land under ``<user_data>/OutPut/<kind>/`` (user can
    actually find it) with a collision-proof name. Falls back to the system
    temp dir if the paths module is unavailable (standalone scripts) or the
    OutPut dir can't be created (read-only profile)."""
    if kind:
        try:
            from paths import output_dir  # type: ignore[import-not-found]

            ts = int(time.time())
            uniq = _short_hash(_next_seed_uniq(), 4)
            return output_dir(kind) / f"{prefix}-{ts}-{uniq}{suffix}"
        except Exception:  # noqa: BLE001 — fall back to temp
            pass
    return auto_temp_path(prefix, suffix)


def resolve_for_write(
    p: Optional[str | os.PathLike[str]],
    *,
    default_prefix: str,
    default_suffix: str,
    default_kind: str = "",
) -> Path:
    """Resolve an output path for a *new* file.

    Rules:
      * ``p`` is None  → auto-named file. With ``default_kind`` set, lands
                          under ``<user_data>/OutPut/<kind>/`` (mirrors the
                          PPT behavior so users can find it); otherwise the
                          system temp dir.
      * ``p`` given    → its parent directory must be authorized OR be
                          the temp dir; and the path must not fall under
                          a system directory.

    Raises :class:`PathError` when the target is not writable per policy.
    """
    if not p:
        return _default_output_path(default_prefix, default_suffix, default_kind)

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


# ---------------------------------------------------------------------------
# WI-T1.5 last-mile artifact 默认保存路径 + title_slug
# 详见 plans/2026-05-23-tool-last-mile-upgrade/00-PRD.md §3 D4 (v2.1)
# ---------------------------------------------------------------------------

# 非法 FS 字符（Windows 通用）+ 控制字符
_ILLEGAL_FS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
# 任意空白序列
_WHITESPACE = re.compile(r"\s+")
# 多个连续 '-'
_MULTI_DASH = re.compile(r"-+")


def title_slug(title: str, *, max_grapheme: int = 60) -> str:
    """PRD §3 D4 title_slug 规则。

    1. NFC 标准化（é 合成形式与分解形式归一）
    2. 非法 FS 字符 ``<>:"/\\|?*`` + 控制字符（\\x00..\\x1f）替换为 ``-``
    3. 空白折叠为 ``-``
    4. 多个连续 ``-`` 折叠为单个，去头尾
    5. 截至 ``max_grapheme`` 字符（简化版：用 char 长度，emoji 仍可能截半
       但实践 OK；未来可用 regex \\X 升级）
    6. 空 / 全非法 → ``"untitled"``

    保留：中文 / 常用 emoji / 数字 / 字母 / 下划线 / 短横线 / 感叹号等。
    """
    if not title:
        return "untitled"
    s = unicodedata.normalize("NFC", title)
    s = _ILLEGAL_FS_CHARS.sub("-", s)
    s = _WHITESPACE.sub("-", s.strip())
    s = _MULTI_DASH.sub("-", s).strip("-")
    if not s:
        return "untitled"
    if len(s) > max_grapheme:
        s = s[:max_grapheme].rstrip("-")
        if not s:
            return "untitled"
    return s


def _short_hash(seed: str, length: int = 8) -> str:
    """sha256 → 前 N hex 字符（用于文件名 collision 避免）。"""
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:length]


# nanos 计数器：同一进程内 time.time_ns() 在 Windows 上可能有 100ns 粒度，
# 高并发下两次调用可能拿到同样 ns；用单调递增计数补足熵。
_counter_lock = threading.Lock()
_counter = 0


def _next_seed_uniq() -> str:
    """生成进程内单调递增的 seed 片段（防 ns 粒度碰撞）。"""
    global _counter
    with _counter_lock:
        _counter += 1
        c = _counter
    return f"{time.time_ns()}:{os.getpid()}:{c}"


def artifact_default_path(
    *,
    tool_name: str,
    title: str,
    ext: str,
    artifact_dir: str = "",
    seed: Optional[str] = None,
) -> Path:
    """WI-T1.5 last-mile artifact 默认保存路径（PRD §3 D4）。

    模式：``<artifact_dir>/<YYYY-MM-DD>/<tool_name>/<title_slug>-<8hex>.<ext>``

    ``artifact_dir`` 为空 → fallback 到旧 :func:`auto_temp_path`（BC 保证：
    last_mile flag 全 OFF 时行为不变）。

    Args:
        tool_name: 工具名（落地为目录名）；非法 FS 字符按 title_slug 规则清洗。
        title: 用户给的产物标题（如 "营销周报 📊"）；为空时 slug 自动 'untitled'。
        ext: 文件扩展名（前导 ``.`` 可省）。
        artifact_dir: PRD D4 配置项，留空走旧 tempdir。
        seed: 可选哈希种子；为空时用 time_ns + pid + counter（防并发碰撞）。

    Returns:
        绝对路径；父目录已 ``mkdir -p``。
    """
    ext_str = ext.lstrip(".")
    if not artifact_dir:
        return auto_temp_path(tool_name, "." + ext_str)

    base = Path(artifact_dir).expanduser().resolve()
    date_dir = time.strftime("%Y-%m-%d")
    parent = base / date_dir / title_slug(tool_name)
    parent.mkdir(parents=True, exist_ok=True)

    slug = title_slug(title)
    seed_str = seed or _next_seed_uniq()
    fname = f"{slug}-{_short_hash(seed_str)}.{ext_str}"
    return parent / fname


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
    # WI-T1.5
    "artifact_default_path",
    "title_slug",
]
