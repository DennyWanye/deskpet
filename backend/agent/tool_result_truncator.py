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

import secrets
from collections import OrderedDict
from typing import Optional, Tuple


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


class ToolResultRefStore:
    """LRU-backed store of full tool_result bodies keyed by ref_id.

    Tiny in-memory store (no SQLite, no asyncio). The agent loop calls
    ``put(content)`` during truncation and ``get(ref_id, start, end)``
    when the LLM (via a future fetch tool) wants to read more.
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
        if self._cap <= 0:
            return ref
        if len(self._store) >= self._cap:
            # popitem(last=False) drops least-recently-used.
            self._store.popitem(last=False)
        self._store[ref] = content
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
            return None
        # Touch — promote to most-recently-used so subsequent calls keep
        # this ref hot.
        self._store.move_to_end(ref_id)
        if start is None and end is None:
            return content
        s = max(0, start if start is not None else 0)
        e = end if end is not None else len(content)
        e = max(s, min(len(content), e))
        return content[s:e]

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
