# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P5-S2 B1 — Tool result truncator + ref_id store tests.

Contract:
- maybe_truncate(content, threshold, head_chars, tail_chars) returns
  (truncated_content, ref_id_or_None).
- Below threshold → returns content unchanged + ref_id=None.
- Above threshold → returns "<head>...[truncated N chars, ref_id=X]...<tail>"
  + a non-None ref_id. The full body is stored in the supplied store.
- ToolResultRefStore.get(ref_id) returns the original content.
- ToolResultRefStore.get with range=(start, end) returns slice.
- Old refs are evicted when capacity is reached (LRU).
"""
from __future__ import annotations

import pytest

from agent.tool_result_truncator import (
    ToolResultRefStore,
    maybe_truncate_tool_result,
    DEFAULT_THRESHOLD,
    DEFAULT_HEAD,
    DEFAULT_TAIL,
)


# ---------- pure truncation function ----------


def test_below_threshold_returns_unchanged():
    store = ToolResultRefStore()
    content = "a" * (DEFAULT_THRESHOLD - 1)
    out, ref = maybe_truncate_tool_result(content, store=store)
    assert out == content
    assert ref is None


def test_at_threshold_unchanged():
    """Boundary: exactly at threshold is still inline (not truncated)."""
    store = ToolResultRefStore()
    content = "a" * DEFAULT_THRESHOLD
    out, ref = maybe_truncate_tool_result(content, store=store)
    assert out == content
    assert ref is None


def test_above_threshold_truncates_and_stores():
    store = ToolResultRefStore()
    content = "HEAD_" + ("x" * 6000) + "_TAIL"
    out, ref = maybe_truncate_tool_result(content, store=store)
    assert ref is not None
    assert "[truncated" in out
    assert ref in out  # ref_id is embedded in the truncation marker
    # Head and tail of original are preserved
    assert out.startswith("HEAD_")
    assert out.endswith("_TAIL")
    # Full body is recoverable from store
    assert store.get(ref) == content


def test_truncation_shorter_than_original():
    store = ToolResultRefStore()
    content = "a" * 10000
    out, _ref = maybe_truncate_tool_result(content, store=store)
    assert len(out) < len(content)
    # Head + tail + marker ≈ DEFAULT_HEAD + DEFAULT_TAIL + ~100 chars marker
    assert len(out) <= DEFAULT_HEAD + DEFAULT_TAIL + 200


def test_custom_thresholds_work():
    store = ToolResultRefStore()
    content = "a" * 500
    out, ref = maybe_truncate_tool_result(
        content, store=store, threshold=100, head_chars=20, tail_chars=10
    )
    assert ref is not None
    assert out.startswith("a" * 20)
    assert out.endswith("a" * 10)


def test_two_calls_produce_different_ref_ids():
    store = ToolResultRefStore()
    c1 = "x" * 5000
    c2 = "y" * 5000
    _, r1 = maybe_truncate_tool_result(c1, store=store)
    _, r2 = maybe_truncate_tool_result(c2, store=store)
    assert r1 != r2
    assert store.get(r1) == c1
    assert store.get(r2) == c2


# ---------- ref store API ----------


def test_store_get_unknown_returns_none():
    store = ToolResultRefStore()
    assert store.get("nonexistent") is None


def test_store_get_with_range():
    store = ToolResultRefStore()
    content = "0123456789" * 100  # 1000 chars
    ref = store.put(content)
    # Range [10, 30) → "0123456789012345678901234567"... wait, content is "0123456789" repeated
    # Let's check exact: content[10:30] = "0123456789" * 2 = "01234567890123456789"
    assert store.get(ref, start=10, end=30) == content[10:30]


def test_store_get_range_clamps_to_bounds():
    store = ToolResultRefStore()
    content = "abc"
    ref = store.put(content)
    assert store.get(ref, start=0, end=999) == "abc"
    assert store.get(ref, start=-5, end=2) == "ab"


def test_store_lru_evicts_oldest():
    """LRU cap — when full, oldest unread entry is dropped to make room.

    No reads between puts: insertion order = LRU order, so r1 is oldest
    when r4 is added.
    """
    store = ToolResultRefStore(max_entries=3)
    r1 = store.put("first")
    r2 = store.put("second")
    r3 = store.put("third")
    r4 = store.put("fourth")  # evicts r1 (no touches happened)
    assert store.get(r1) is None
    assert store.get(r2) == "second"
    assert store.get(r3) == "third"
    assert store.get(r4) == "fourth"


def test_store_get_refreshes_lru_position():
    """Reading r1 makes it recently used → r2 evicted next."""
    store = ToolResultRefStore(max_entries=3)
    r1 = store.put("first")
    r2 = store.put("second")
    r3 = store.put("third")
    # Touch r1 so it becomes most-recent
    assert store.get(r1) == "first"
    # Add r4 → evict r2 (least recently used)
    r4 = store.put("fourth")
    assert store.get(r1) == "first"
    assert store.get(r2) is None
    assert store.get(r3) == "third"
    assert store.get(r4) == "fourth"


def test_ref_id_format_is_short_and_safe():
    """ref_id should be URL-safe + short enough to not bloat the message."""
    store = ToolResultRefStore()
    ref = store.put("anything")
    # 8-12 char base32 / hex chunk
    assert 6 <= len(ref) <= 24
    assert all(c.isalnum() or c in "_-" for c in ref)
