"""Unit tests for llm.budget (spend tracking, UTC rollover, warning thresholds)."""
from __future__ import annotations

import datetime as _dt
import json

import pytest

import llm.budget as budget_mod
from llm.budget import DailyBudget
from llm.types import ChatUsage


def _usage_for_cost(target_usd: float, *, provider: str = "openai", model: str = "gpt-4o") -> ChatUsage:
    """Build a ChatUsage that pricing evaluates to ~target_usd.

    gpt-4o priced at $10 / 1M output ⇒ 100k output_tokens ≈ $1.00.
    """
    output_tokens = int(target_usd * 100_000)
    return ChatUsage(input_tokens=0, output_tokens=output_tokens)


def test_fresh_state_starts_at_zero(tmp_path):
    b = DailyBudget(cap_usd=10.0, state_path=tmp_path / "state.json")
    assert b.get_spent() == 0.0
    assert b.check_allowed() is True
    assert b.warning_threshold_crossed() is None


def test_warning_at_80_percent(tmp_path):
    b = DailyBudget(cap_usd=10.0, state_path=tmp_path / "state.json")
    spent = b.add_usage("openai", "gpt-4o", _usage_for_cost(8.0))
    assert spent == pytest.approx(8.0, rel=1e-3)
    crossed = b.warning_threshold_crossed()
    assert crossed == 0.8
    # Idempotent — the second call MUST NOT re-warn.
    assert b.warning_threshold_crossed() is None


def test_block_at_100_percent(tmp_path):
    b = DailyBudget(cap_usd=10.0, state_path=tmp_path / "state.json")
    b.add_usage("openai", "gpt-4o", _usage_for_cost(10.0))
    assert b.check_allowed() is False
    # 100% crossing reported once.
    assert b.warning_threshold_crossed() == 1.0
    assert b.warning_threshold_crossed() is None


def test_warning_cascade_80_then_100(tmp_path):
    b = DailyBudget(cap_usd=10.0, state_path=tmp_path / "state.json")
    b.add_usage("openai", "gpt-4o", _usage_for_cost(8.5))
    assert b.warning_threshold_crossed() == 0.8
    b.add_usage("openai", "gpt-4o", _usage_for_cost(2.0))
    assert b.warning_threshold_crossed() == 1.0


def test_persists_across_restart(tmp_path):
    state = tmp_path / "state.json"
    b1 = DailyBudget(cap_usd=10.0, state_path=state)
    b1.add_usage("openai", "gpt-4o", _usage_for_cost(3.0))
    assert b1.get_spent() == pytest.approx(3.0, rel=1e-3)

    # Second instance reads the same file.
    b2 = DailyBudget(cap_usd=10.0, state_path=state)
    assert b2.get_spent() == pytest.approx(3.0, rel=1e-3)


def test_utc_rollover_resets(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    # Pre-seed state with yesterday's date.
    yesterday = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
    state.write_text(json.dumps({"utc_date": yesterday, "spent_usd": 9.5, "entries": []}))
    b = DailyBudget(cap_usd=10.0, state_path=state)
    # Constructor detects stale date and resets.
    assert b.get_spent() == 0.0
    assert b.check_allowed() is True


def test_rollover_mid_session(tmp_path, monkeypatch):
    """get_spent called AFTER date changes MUST return 0, not yesterday's total."""
    state = tmp_path / "state.json"
    b = DailyBudget(cap_usd=10.0, state_path=state)
    b.add_usage("openai", "gpt-4o", _usage_for_cost(5.0))
    assert b.get_spent() == pytest.approx(5.0, rel=1e-3)

    # Simulate a UTC date shift without actually sleeping 24h by
    # monkey-patching the helper.
    tomorrow = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=1)).strftime("%Y-%m-%d")
    monkeypatch.setattr(DailyBudget, "_utc_date_str", staticmethod(lambda: tomorrow))
    assert b.get_spent() == 0.0


def test_cache_read_discount_applies(tmp_path):
    """Anthropic cache_read tokens MUST be billed at the discounted rate."""
    b = DailyBudget(cap_usd=10.0, state_path=tmp_path / "state.json")
    # 1M input @ $3 = $3.00
    b.add_usage(
        "anthropic",
        "claude-sonnet-4-5",
        ChatUsage(input_tokens=1_000_000),
    )
    first = b.get_spent()
    b.reset()
    # 1M cache_read @ $0.30 = $0.30
    b.add_usage(
        "anthropic",
        "claude-sonnet-4-5",
        ChatUsage(cache_read_tokens=1_000_000),
    )
    second = b.get_spent()
    assert first > second  # cache read is cheaper
    assert second == pytest.approx(0.30, rel=1e-2)


def test_unknown_model_uses_pessimistic_fallback(tmp_path):
    b = DailyBudget(cap_usd=10.0, state_path=tmp_path / "state.json")
    b.add_usage("anthropic", "claude-next-gen-xyzzy", ChatUsage(output_tokens=1_000_000))
    # UNKNOWN_MODEL_PRICE output = $30 / 1M ⇒ $30. Over cap so check_allowed False.
    assert b.get_spent() >= 10.0
    assert b.check_allowed() is False
