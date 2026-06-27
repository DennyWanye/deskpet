# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P5-S2 B2 — Conversation history compactor.

Why
---
Long code-mode sessions snowball: each iteration re-sends the entire
conversation history. We observed 33 iterations × ~10 KB tool_result
each → context grew to hundreds of KB, blowing through context windows
and amplifying mid-stream-drop failures.

Strategy
--------
Periodically (when the chat handler decides we've crossed a threshold)
summarize the OLD middle of the conversation into a single system
message and drop the originals. We always preserve:

  1. **All initial ``system`` messages** — these include the deskpet
     persona, tool-use guidance, plan-injected instructions. Cannot
     drop or summarize away or the model loses its anchors.

  2. **The last ``keep_recent`` non-system messages** — the model
     needs recent user/assistant/tool turns verbatim because reasoning
     chains often span 2-3 turns.

The middle window is what gets summarized via a caller-supplied
``summarize_fn``. The caller owns the LLM call (we don't import
providers — that would tangle test isolation).

This module is pure-Python on the public API: ``should_compact``,
``select_compactable_range``, ``inject_summary`` are sync; only the
high-level orchestrator ``compact_messages`` is async because the
summarize step is.

Failure mode
------------
If ``summarize_fn`` raises (LLM transient error during compaction),
we **return the original messages unchanged**. Better to keep a long
context than to silently lose history. The caller should retry on
the next iteration.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Tuple


logger = logging.getLogger(__name__)


# Defaults match the production failure profile: ~15 iterations + ~64 KB
# context was where we started seeing the relay proxy resets in the wild.
DEFAULT_MESSAGE_THRESHOLD = 20
DEFAULT_CHAR_THRESHOLD = 60_000
DEFAULT_KEEP_RECENT = 6


SummarizeFn = Callable[[str], Awaitable[str]]


def should_compact(
    messages: list[dict[str, Any]],
    *,
    message_threshold: int = DEFAULT_MESSAGE_THRESHOLD,
    char_threshold: int = DEFAULT_CHAR_THRESHOLD,
) -> bool:
    """Decide whether the history is large enough to bother compacting.

    Returns True if EITHER the count or total char length exceeds its
    threshold. Either signal alone is enough — e.g. 50 small messages
    can still trigger thrashing on the proxy, and 3 huge messages can
    blow the context window.
    """
    if len(messages) > message_threshold:
        return True
    total_chars = sum(len(str(m.get("content", ""))) for m in messages)
    return total_chars > char_threshold


def select_compactable_range(
    messages: list[dict[str, Any]],
    *,
    keep_recent: int = DEFAULT_KEEP_RECENT,
) -> Tuple[int, int]:
    """Return (start, end) — exclusive — of the contiguous middle range.

    The slice ``messages[start:end]`` is what we'll summarize. Anything
    before ``start`` (the system stack) and anything from ``end`` onward
    (the recent tail) is preserved verbatim.

    If there are fewer messages than ``keep_recent`` + system stack,
    returns ``(start, start)`` (empty range) — nothing to compact.
    """
    n = len(messages)

    # Find the end of the leading system stack.
    sys_end = 0
    while sys_end < n and messages[sys_end].get("role") == "system":
        sys_end += 1

    # Tail boundary: keep last ``keep_recent`` non-system messages.
    tail_start = max(sys_end, n - max(0, int(keep_recent)))

    if tail_start <= sys_end:
        return (sys_end, sys_end)  # empty range

    return (sys_end, tail_start)


def inject_summary(
    messages: list[dict[str, Any]],
    compact_range: Tuple[int, int],
    summary_text: str,
) -> list[dict[str, Any]]:
    """Return a NEW list with messages[start:end] replaced by a single
    system message containing ``summary_text``.

    The summary message is placed AT ``start`` — i.e. right after the
    existing system stack, before the kept recent tail.

    Does NOT mutate the input list (defensive — the agent loop may
    still hold a reference for tool_result rebinding).
    """
    start, end = compact_range
    if start >= end or not summary_text:
        return list(messages)  # shallow copy, no-op

    summarized_count = end - start
    summary_msg = {
        "role": "system",
        "content": (
            f"[Summary of {summarized_count} earlier turns — compacted to "
            f"save context budget]\n\n{summary_text}"
        ),
        "_is_history_summary": True,
    }
    return list(messages[:start]) + [summary_msg] + list(messages[end:])


async def compact_messages(
    messages: list[dict[str, Any]],
    *,
    summarize_fn: SummarizeFn,
    message_threshold: int = DEFAULT_MESSAGE_THRESHOLD,
    char_threshold: int = DEFAULT_CHAR_THRESHOLD,
    keep_recent: int = DEFAULT_KEEP_RECENT,
    goal_text: str | None = None,
) -> list[dict[str, Any]]:
    """High-level entry point — checks threshold, selects range, asks
    ``summarize_fn`` for a summary, and returns the rewritten list.

    On any failure, returns the original list unchanged (safe fallback).

    ``goal_text`` (WI-1.3): when non-empty, appends a ``[目标锚定]`` system
    message after the rewritten history so the model stays anchored to the
    active goal even after context compaction.  Appended only on the success
    path (summarize succeeded, inject_summary returned a modified list).
    BC: ``goal_text=None`` (default) → output byte-identical to old behaviour.
    """
    if not should_compact(
        messages,
        message_threshold=message_threshold,
        char_threshold=char_threshold,
    ):
        return list(messages)

    start, end = select_compactable_range(messages, keep_recent=keep_recent)
    if start >= end:
        return list(messages)

    # Build the summarize prompt: just concatenate the message contents
    # with role tags so the LLM sees who said what.
    to_summarize = _format_for_summarize(messages[start:end])

    try:
        summary = await summarize_fn(to_summarize)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "history_compactor: summarize_fn failed — keeping original "
            "history. err=%s", str(exc)[:200],
        )
        return list(messages)

    if not summary or not summary.strip():
        return list(messages)

    rewritten = inject_summary(messages, (start, end), summary.strip())

    # WI-1.3 goal anchor: after a successful compaction, if there is an
    # active goal, append a system message reminding the model of the goal
    # so it doesn't drift after losing the middle context.
    if goal_text and goal_text.strip():
        anchor_msg: dict[str, Any] = {
            "role": "system",
            "content": (
                "[目标锚定] 当前目标：" + goal_text.strip()
                + "\n请确保接下来的动作仍服务于上述目标，不要被中间步骤带偏。"
            ),
        }
        rewritten = list(rewritten) + [anchor_msg]

    return rewritten


def _format_for_summarize(messages: list[dict[str, Any]]) -> str:
    """Stringify role+content pairs in a format the summarizer LLM can
    digest. Compact (no JSON wrapping) since we're trying to SAVE tokens,
    not add wrapping overhead.
    """
    out = []
    for m in messages:
        role = m.get("role", "?")
        content = str(m.get("content", ""))
        # Tool calls have their args in the assistant message; include
        # them if present so the summary captures what was attempted.
        tool_calls = m.get("tool_calls")
        if tool_calls:
            content = content + f"\n[tool_calls: {len(tool_calls)} calls]"
        out.append(f"{role.upper()}: {content}")
    return "\n\n".join(out)
