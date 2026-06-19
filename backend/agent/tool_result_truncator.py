# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P5-S2 B1 — Tool result truncator + ref_id store.

Why
---
Long tool_result bodies (``read_file`` on a 60 KB Go source, ``run_shell``
producing dense compiler output, etc.) get appended verbatim to
``working_messages`` in the agent loop and re-sent on every iteration.
33 iterations × 10 KB tool_result quickly explodes context to hundreds
of KB tokens, blowing through the model's context window and amplifying
mid-stream-drop / proxy idle-timeout failures.

Strategy
--------
For each tool_result body, if it exceeds ``threshold`` chars:

  1. Keep ``head_chars`` from the start and ``tail_chars`` from the end —
     usually enough for the LLM to see the structure (function signatures,
     final stack trace) without re-receiving the whole file.

  2. Replace the middle with a marker including a stable ``ref_id``:

         <head>...[truncated N chars, ref_id=<ref>]...<tail>

     The full body is stored in :class:`ToolResultRefStore` keyed by
     ``ref_id`` so the LLM (via a follow-up tool call) or the UI can
     fetch the original on demand.

  3. LLM-facing follow-up: a future ``fetch_tool_result(ref_id, start,
     end)`` tool retrieves slices on demand. Out-of-scope for this slice;
     this module just owns the truncation + store primitives.

Design choices
--------------
* In-memory ``OrderedDict`` LRU keeps the implementation tiny and fast.
  ``max_entries`` is a soft cap; per-session caps belong in the chat
  handler. We deliberately do not persist refs to SessionDB — refs are
  ephemeral within a single deskpet run; persistence is not worth the
  table-design cost for the marginal "user wants to ref a result after
  restart" case.

* ``ref_id`` is an 8-char base36 token from ``secrets.token_urlsafe``.
  Short enough to not pollute the message, long enough to avoid
  collisions across thousands of tool results.

* No async lock — the agent loop is single-threaded per session; we can
  add an ``asyncio.Lock`` later if cross-session shared store is needed.
"""
from __future__ import annotations

import logging
import secrets
from collections import OrderedDict
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


# Defaults tuned to the empirical thresholds we saw in production:
#   * threshold 4000 chars matches deepseek-v4-pro's "JSON escape sweet spot"
#     boundary — below 3KB args are reliable, above 4KB starts to fail.
#   * head 1500 chars typically includes file headers / import blocks /
#     function signatures — enough for LLM to recall the file's shape.
#   * tail 500 chars catches trailing errors, last stack frame, final lines.
DEFAULT_THRESHOLD = 4000
DEFAULT_HEAD = 1500
DEFAULT_TAIL = 500
DEFAULT_MAX_ENTRIES = 256


# Module-level singleton — used by BOTH:
#   1. AgentLoop, which truncates long tool_result bodies before they
#      land in working_messages.
#   2. The ``fetch_tool_result`` tool, which the LLM calls to retrieve
#      the full body (or a slice) by ref_id.
#
# A module singleton (vs. per-AgentLoop instance) lets the fetch tool
# resolve refs without plumbing the store through tool dispatch. The
# store is keyed by random ref_id so cross-session collision is
# astronomically unlikely; LRU eviction (256 entries) bounds memory.
_GLOBAL_REF_STORE: "ToolResultRefStore" | None = None


def get_global_ref_store() -> "ToolResultRefStore":
    """Lazy-init and return the module singleton."""
    global _GLOBAL_REF_STORE
    if _GLOBAL_REF_STORE is None:
        _GLOBAL_REF_STORE = ToolResultRefStore()
    return _GLOBAL_REF_STORE


def _spill_dir() -> Optional[Path]:
    """落盘目录 <user_data>/cache/tool_refs/。paths 不可用(独立脚本) → None。

    pytest 进程内默认禁用(防全套测试把假数据写进真实用户 cache 目录);
    落盘专项测试 monkeypatch 本函数注入 tmp 目录。
    """
    try:
        import os as _os

        if _os.environ.get("PYTEST_CURRENT_TEST"):
            return None
        from paths import user_cache_dir  # type: ignore[import-not-found]

        d = Path(user_cache_dir()) / "tool_refs"
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception:  # noqa: BLE001
        return None


_SPILL_MAX_FILES = 400  # 目录文件数上限,超出按 mtime 清最老


class ToolResultRefStore:
    """LRU 内存 + 磁盘 spill 的全文 tool_result 存储,按 ref_id 取。

    2026-06-13 上下文外置(治本): put() 同时落盘
    ``<user_data>/cache/tool_refs/<ref>.txt`` —— 内存 LRU 淘汰/进程重启
    后 ref 仍可经 fetch_tool_result 取回(压缩摘要里的 ref_id 不再失效)。
    get() 内存 miss 时读盘回填。落盘失败静默退化为纯内存(行为同旧版)。
    """

    def __init__(self, *, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self._cap = int(max_entries)
        # OrderedDict gives us O(1) move-to-end (LRU update) + popitem(last=False).
        self._store: "OrderedDict[str, str]" = OrderedDict()

    def put(self, content: str) -> str:
        """Store the full body, return a fresh ref_id. Evicts oldest if full."""
        ref = self._new_ref_id()
        # Edge case: if cap is 0, refuse to store but still return a ref so
        # callers don't crash. The marker will be in the truncated message,
        # the body is just unavailable.
        if self._cap > 0:
            if len(self._store) >= self._cap:
                # popitem(last=False) drops least-recently-used.
                self._store.popitem(last=False)
            self._store[ref] = content
        self._spill_write(ref, content)
        return ref

    def get(
        self,
        ref_id: str,
        *,
        start: Optional[int] = None,
        end: Optional[int] = None,
    ) -> Optional[str]:
        """Retrieve full body (or a slice). Returns None if ref_id unknown.

        ``start``/``end`` follow Python slice semantics; out-of-bound
        values are clamped (no IndexError).
        """
        content = self._store.get(ref_id)
        if content is None:
            # 内存 miss(LRU 淘汰/重启) → 读盘回填
            content = self._spill_read(ref_id)
            if content is None:
                return None
            if self._cap > 0:
                if len(self._store) >= self._cap:
                    self._store.popitem(last=False)
                self._store[ref_id] = content
        # Touch — promote to most-recently-used so subsequent calls keep
        # this ref hot.
        if ref_id in self._store:
            self._store.move_to_end(ref_id)
        if start is None and end is None:
            return content
        s = max(0, start if start is not None else 0)
        e = end if end is not None else len(content)
        e = max(s, min(len(content), e))
        return content[s:e]

    # ── disk spill ──────────────────────────────────────────────

    @staticmethod
    def _spill_write(ref: str, content: str) -> None:
        try:
            d = _spill_dir()
            if d is None:
                return
            (d / f"{ref}.txt").write_text(content, encoding="utf-8")
            # 容量管理: 超上限按 mtime 清最老(best-effort)
            files = sorted(d.glob("*.txt"), key=lambda p: p.stat().st_mtime)
            for old in files[: max(0, len(files) - _SPILL_MAX_FILES)]:
                try:
                    old.unlink()
                except OSError:
                    pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("tool_ref spill write failed: %s", exc)

    @staticmethod
    def _spill_read(ref: str) -> Optional[str]:
        try:
            d = _spill_dir()
            if d is None:
                return None
            f = d / f"{ref}.txt"
            if not f.is_file():
                return None
            return f.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.debug("tool_ref spill read failed: %s", exc)
            return None

    def _new_ref_id(self) -> str:
        # 6 bytes → 8 URL-safe chars. Collision rate negligible for our
        # working-set sizes (~256 entries × per-session lifetime).
        return secrets.token_urlsafe(6)


def maybe_truncate_tool_result(
    content: str,
    *,
    store: ToolResultRefStore,
    threshold: int = DEFAULT_THRESHOLD,
    head_chars: int = DEFAULT_HEAD,
    tail_chars: int = DEFAULT_TAIL,
) -> Tuple[str, Optional[str]]:
    """Truncate ``content`` if it exceeds ``threshold``.

    Returns ``(message_content, ref_id_or_None)``. The returned
    ``message_content`` is what should be appended to working_messages
    — short-form for long bodies, original for short ones.

    The contract is intentionally simple:
      * len(content) <= threshold → no-op, ref_id is None.
      * Otherwise → trim middle, return marker, and the second tuple
        element is the ref_id under which the full body is stored.
    """
    if not isinstance(content, str):
        # Defensive: dispatch_tool sometimes hands us bytes / dicts that
        # leaked past serialization. Coerce safely.
        try:
            content = str(content)
        except Exception:
            return ("<unserializable tool_result>", None)

    if len(content) <= threshold:
        return (content, None)

    head = content[:head_chars]
    tail = content[-tail_chars:] if tail_chars > 0 else ""
    truncated_len = len(content) - len(head) - len(tail)
    ref_id = store.put(content)
    marker = (
        f"\n...[truncated {truncated_len} chars; "
        f"ref_id={ref_id} — use fetch_tool_result to read more]...\n"
    )
    return (head + marker + tail, ref_id)
