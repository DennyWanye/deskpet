"""P5-S2 B2 — Conversation history compactor tests.

Contract:
- ``should_compact(messages, threshold_messages, threshold_chars)`` returns
  True when either the message count or total char length exceeds its
  threshold.
- ``select_compactable_range(messages, keep_recent)`` returns the
  (start_idx, end_idx) of messages that can be safely summarized:
  * preserves all initial ``system`` messages (system stack)
  * preserves the last ``keep_recent`` non-system messages
  * a contiguous middle window is what gets summarized
- ``inject_summary(messages, range, summary_text)`` returns a new list
  with the summarized range replaced by a single system message of
  ``"[Summary of N earlier turns]: <summary_text>"``.
- The summary system message is placed AFTER the existing system stack
  so model still sees its primary instructions first.

These are pure functions — the actual LLM call to generate the summary
is delegated to a caller-provided coroutine ``summarize_fn`` and
``compact_messages`` is the orchestration wrapper.
"""
from __future__ import annotations

import pytest

from agent.history_compactor import (
    should_compact,
    select_compactable_range,
    inject_summary,
    DEFAULT_MESSAGE_THRESHOLD,
    DEFAULT_CHAR_THRESHOLD,
    DEFAULT_KEEP_RECENT,
)


def _make_msg(role: str, content: str) -> dict:
    return {"role": role, "content": content}


# ---------- should_compact ----------


def test_below_both_thresholds_no_compact():
    msgs = [_make_msg("user", "hi"), _make_msg("assistant", "hello")]
    assert should_compact(msgs) is False


def test_message_count_threshold_triggers():
    msgs = [_make_msg("user", "x")] * (DEFAULT_MESSAGE_THRESHOLD + 1)
    assert should_compact(msgs) is True


def test_char_threshold_triggers():
    msgs = [_make_msg("user", "x" * (DEFAULT_CHAR_THRESHOLD + 1))]
    assert should_compact(msgs) is True


def test_custom_thresholds():
    msgs = [_make_msg("user", "x")] * 5
    assert should_compact(msgs, message_threshold=3) is True
    assert should_compact(msgs, message_threshold=10) is False


# ---------- select_compactable_range ----------


def test_select_preserves_system_stack_and_recent():
    msgs = [
        _make_msg("system", "sys1"),
        _make_msg("system", "sys2"),
        _make_msg("user", "u1"),
        _make_msg("assistant", "a1"),
        _make_msg("user", "u2"),
        _make_msg("assistant", "a2"),
        _make_msg("user", "u3"),
        _make_msg("assistant", "a3"),
        _make_msg("user", "u4"),
        _make_msg("assistant", "a4"),
    ]
    # Keep last 2 non-system → drop msgs[2..7] (u1, a1, u2, a2, u3, a3)
    start, end = select_compactable_range(msgs, keep_recent=2)
    assert start == 2
    assert end == 8  # exclusive; 8 = first kept index
    # Sanity: msgs[start:end] are 6 entries
    assert end - start == 6


def test_select_no_system_messages():
    msgs = [_make_msg("user", str(i)) for i in range(10)]
    start, end = select_compactable_range(msgs, keep_recent=3)
    assert start == 0
    assert end == 7  # keep last 3


def test_select_returns_empty_range_when_nothing_to_compact():
    """If there are fewer messages than keep_recent + system stack, range is empty."""
    msgs = [
        _make_msg("system", "s"),
        _make_msg("user", "u1"),
        _make_msg("assistant", "a1"),
    ]
    start, end = select_compactable_range(msgs, keep_recent=5)
    # No middle to compact
    assert start == end


def test_select_default_keep_recent_constant():
    msgs = [_make_msg("user", str(i)) for i in range(20)]
    start, end = select_compactable_range(msgs)
    # Default keep_recent should leave a sensible window
    assert end - start > 0
    assert end == 20 - DEFAULT_KEEP_RECENT


# ---------- inject_summary ----------


def test_inject_summary_replaces_range_with_system_msg():
    msgs = [
        _make_msg("system", "sys1"),
        _make_msg("user", "u1"),
        _make_msg("assistant", "a1"),
        _make_msg("user", "u2"),
        _make_msg("assistant", "a2"),
    ]
    out = inject_summary(msgs, (1, 4), "summary text here")
    # Expected: [sys1, summary_system, a2]   (range was u1, a1, u2)
    assert len(out) == 3
    assert out[0]["role"] == "system"
    assert out[0]["content"] == "sys1"
    assert out[1]["role"] == "system"
    assert "summary text here" in out[1]["content"]
    assert "Summary of 3 earlier turns" in out[1]["content"] or "3" in out[1]["content"]
    assert out[2] == msgs[4]


def test_inject_summary_empty_range_no_op():
    msgs = [_make_msg("user", "a"), _make_msg("assistant", "b")]
    out = inject_summary(msgs, (1, 1), "irrelevant")
    assert out == msgs


def test_inject_summary_preserves_message_order():
    msgs = [
        _make_msg("system", "S"),
        _make_msg("user", "u1"),
        _make_msg("assistant", "a1"),
        _make_msg("tool", "t1"),
        _make_msg("user", "u2"),
        _make_msg("assistant", "a2"),
    ]
    out = inject_summary(msgs, (1, 4), "compacted u1+a1+t1")
    # Resulting order: [S, summary, u2, a2]
    roles = [m["role"] for m in out]
    assert roles == ["system", "system", "user", "assistant"]


def test_inject_summary_does_not_mutate_input():
    msgs = [_make_msg("user", "u1"), _make_msg("assistant", "a1")]
    original = list(msgs)
    inject_summary(msgs, (0, 1), "s")
    # Caller's list still intact
    assert msgs == original


# ---------- end-to-end: orchestration via compact_messages ----------


@pytest.mark.asyncio
async def test_compact_messages_calls_summarize_and_injects():
    from agent.history_compactor import compact_messages

    msgs = [
        _make_msg("system", "S"),
        *[_make_msg("user", f"u{i}") for i in range(8)],
        *[_make_msg("assistant", f"a{i}") for i in range(8)],
    ]
    # 17 messages total; keep_recent=4 → compact 13 middle msgs

    captured = {}

    async def fake_summarize(text_to_summarize: str) -> str:
        captured["input"] = text_to_summarize
        return "FAKE SUMMARY"

    new_msgs = await compact_messages(
        msgs, summarize_fn=fake_summarize, keep_recent=4, message_threshold=5
    )
    # Should have: [S, summary, last-4 originals]
    assert len(new_msgs) == 6
    assert new_msgs[0]["role"] == "system"  # original
    assert new_msgs[1]["role"] == "system"  # summary
    assert "FAKE SUMMARY" in new_msgs[1]["content"]
    # Verify input to summarize fn contained early turns
    assert "u0" in captured["input"]
    assert "a0" in captured["input"]


@pytest.mark.asyncio
async def test_compact_messages_skips_when_below_threshold():
    from agent.history_compactor import compact_messages

    msgs = [_make_msg("user", "u1"), _make_msg("assistant", "a1")]
    called = []

    async def fake_summarize(text):
        called.append(text)
        return "S"

    out = await compact_messages(msgs, summarize_fn=fake_summarize)
    # Below default threshold → no compaction, no LLM call
    assert out == msgs
    assert called == []


@pytest.mark.asyncio
async def test_compact_messages_handles_summarize_failure_gracefully():
    """If summarize_fn raises, we return original messages unchanged."""
    from agent.history_compactor import compact_messages

    msgs = [_make_msg("user", "x")] * (DEFAULT_MESSAGE_THRESHOLD + 5)

    async def failing_summarize(text):
        raise RuntimeError("LLM unavailable")

    out = await compact_messages(msgs, summarize_fn=failing_summarize)
    # Failure path: return original list; better to have a long context
    # than to silently drop history.
    assert out == msgs
