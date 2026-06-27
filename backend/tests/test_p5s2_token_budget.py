# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P5-S2 B3 — Token budget guard tests.

Contract:
- ``estimate_tokens(messages)`` returns a fast (non-tiktoken) char/4
  approximation. Reasonable accuracy for English/Chinese mixed text
  in the 5-15% range; cheap enough to call on every request.
- ``get_context_window(model_name)`` returns the trained context window
  for known models, or a conservative default (8192) for unknown.
- ``BudgetCheck`` enum: OK / WARN / BLOCK based on usage ratio.
- ``check_budget(messages, model, *, warn_pct, block_pct)`` returns
  ``BudgetCheckResult`` with the verdict + estimated tokens + ratio.
"""
from __future__ import annotations

import pytest

from agent.token_budget import (
    BudgetCheck,
    BudgetCheckResult,
    estimate_tokens,
    get_context_window,
    check_budget,
    DEFAULT_WARN_PCT,
    DEFAULT_BLOCK_PCT,
)


# ---------- estimate_tokens ----------


def test_estimate_empty_messages_is_zero():
    assert estimate_tokens([]) == 0


def test_estimate_short_text_proportional():
    # Roughly 4 chars per token. "hello world" = 11 chars → ~2-3 tokens.
    msgs = [{"role": "user", "content": "hello world"}]
    est = estimate_tokens(msgs)
    assert 1 <= est <= 10  # very rough; just sanity-check the order


def test_estimate_scales_with_length():
    short = estimate_tokens([{"role": "user", "content": "a" * 100}])
    long = estimate_tokens([{"role": "user", "content": "a" * 10000}])
    assert long > short * 50  # linear-ish


def test_estimate_handles_missing_content():
    # Defensive: some assistant messages have only tool_calls, no content
    msgs = [{"role": "assistant", "tool_calls": [{"id": "1"}]}]
    est = estimate_tokens(msgs)
    assert est >= 0  # doesn't crash


def test_estimate_counts_tool_calls():
    # Tool call args are part of the wire payload; should not be ignored
    msgs = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "1", "name": "read_file", "args": "x" * 1000}],
        }
    ]
    est = estimate_tokens(msgs)
    # 1000+ chars in tool_calls → should produce a meaningful estimate
    assert est >= 100


# ---------- get_context_window ----------


def test_known_models_have_specific_windows():
    # Common large-context models we deploy
    assert get_context_window("deepseek-v4-pro") >= 32_000
    assert get_context_window("claude-sonnet-4.5") >= 100_000
    assert get_context_window("gpt-5") >= 100_000


def test_unknown_model_falls_back_to_default():
    # Should return a conservative default — small enough to error on the
    # side of compaction, big enough not to hit on normal conversations.
    cw = get_context_window("totally-unknown-model-xyz")
    assert 4000 <= cw <= 16_000


def test_model_name_normalization():
    """Match should be tolerant of provider prefixes / case / minor variants."""
    cw1 = get_context_window("deepseek-v4-pro")
    cw2 = get_context_window("DEEPSEEK-V4-PRO")
    assert cw1 == cw2


# ---------- check_budget ----------


def test_below_warn_returns_ok():
    msgs = [{"role": "user", "content": "x" * 100}]
    result = check_budget(msgs, model="deepseek-v4-pro")
    assert result.verdict == BudgetCheck.OK
    assert result.ratio < DEFAULT_WARN_PCT


# 2026-05-15 (Phase 1.1 followup): these exercise the WARN/BLOCK *ratio
# logic*, not any model's window. They used to hardcode
# model="deepseek-v4-pro" against the then-stale 64K table value; after
# the per-model fix deepseek-v4-pro is 1M, so old payload sizes no
# longer trip the thresholds. Pass explicit context_window= (the new
# authoritative param) to keep the threshold math deterministic and
# decoupled from the model table.
_FIXED_WINDOW = 64_000


def test_above_warn_below_block_returns_warn():
    # warn at 80% = ~51k tokens → ~190k chars lands between warn & block.
    # (FP-2 真机校准后 ASCII ≈3.5 char/token,样本同步从 220k 调 190k)
    msgs = [{"role": "user", "content": "x" * 190_000}]
    result = check_budget(
        msgs, model="deepseek-v4-pro", context_window=_FIXED_WINDOW
    )
    assert result.verdict == BudgetCheck.WARN
    assert DEFAULT_WARN_PCT <= result.ratio < DEFAULT_BLOCK_PCT


def test_above_block_returns_block():
    # 64k × 0.95 × 4 = ~243k chars; 300k guarantees past block.
    msgs = [{"role": "user", "content": "x" * 300_000}]
    result = check_budget(
        msgs, model="deepseek-v4-pro", context_window=_FIXED_WINDOW
    )
    assert result.verdict == BudgetCheck.BLOCK
    assert result.ratio >= DEFAULT_BLOCK_PCT


def test_custom_thresholds():
    msgs = [{"role": "user", "content": "x" * 1000}]
    result = check_budget(
        msgs,
        model="deepseek-v4-pro",
        warn_pct=0.001,
        block_pct=0.002,
        context_window=_FIXED_WINDOW,
    )
    # 250 tokens / 64000 = 0.0039 ratio → above block (0.002)
    assert result.verdict == BudgetCheck.BLOCK


def test_result_includes_actionable_advice_in_block():
    msgs = [{"role": "user", "content": "x" * 300_000}]
    result = check_budget(
        msgs, model="deepseek-v4-pro", context_window=_FIXED_WINDOW
    )
    assert result.verdict == BudgetCheck.BLOCK
    # The result should carry a hint string for the UI/log
    assert result.advice
    assert "context" in result.advice.lower() or "compact" in result.advice.lower()


def test_result_carries_tokens_and_window():
    msgs = [{"role": "user", "content": "x" * 1000}]
    result = check_budget(msgs, model="deepseek-v4-pro")
    assert result.estimated_tokens > 0
    assert result.context_window > 0
    assert 0 <= result.ratio <= 1.0


# ---------- Risk-2 fix: real_prompt_tokens_floor ----------
# The gate used to count only working_messages, missing the fixed base
# (system persona + tool schemas + skill prelude, several thousand tokens
# the provider adds at call time). Real machine: window=5000, actual prompt
# 5680 (113%), but working_messages estimate ~800 → ratio 16% → NO BLOCK.
# The floor = last real prompt_tokens (which DOES include the base) fixes it.


def test_floor_default_zero_is_backward_compatible():
    """floor unset → identical to old working_messages-only behaviour."""
    msgs = [{"role": "user", "content": "x" * 100}]
    base = check_budget(msgs, model="deepseek-v4-pro", context_window=_FIXED_WINDOW)
    with_floor0 = check_budget(
        msgs, model="deepseek-v4-pro", context_window=_FIXED_WINDOW,
        real_prompt_tokens_floor=0,
    )
    assert with_floor0.estimated_tokens == base.estimated_tokens
    assert with_floor0.verdict == base.verdict


def test_floor_raises_estimate_and_triggers_block():
    """Tiny working_messages but a high real-prompt floor (simulating the
    big fixed base) → estimate floored up → BLOCK fires.

    This is the exact production blind spot: working_messages alone is ~25
    tokens (would be OK), but the real prompt was 62k of a 64k window (97%).
    """
    msgs = [{"role": "user", "content": "x" * 100}]  # ~25 tokens
    # Without floor: nowhere near block.
    no_floor = check_budget(
        msgs, model="deepseek-v4-pro", context_window=_FIXED_WINDOW,
    )
    assert no_floor.verdict == BudgetCheck.OK
    # With a real-prompt floor of 62k (97% of 64k window) → BLOCK.
    floored = check_budget(
        msgs, model="deepseek-v4-pro", context_window=_FIXED_WINDOW,
        real_prompt_tokens_floor=62_000,
    )
    assert floored.verdict == BudgetCheck.BLOCK
    assert floored.estimated_tokens == 62_000
    assert floored.ratio >= DEFAULT_BLOCK_PCT


def test_floor_never_lowers_estimate():
    """Floor is a floor, not an override: a small floor must not shrink a
    genuinely large working_messages estimate."""
    big = [{"role": "user", "content": "x" * 300_000}]  # well past block
    result = check_budget(
        big, model="deepseek-v4-pro", context_window=_FIXED_WINDOW,
        real_prompt_tokens_floor=10,  # tiny floor must be ignored
    )
    assert result.verdict == BudgetCheck.BLOCK
    assert result.estimated_tokens > 10
