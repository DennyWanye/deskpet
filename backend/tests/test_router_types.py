"""Tests for router.types — BudgetHook contract skeleton (P2-1-S6).

The contract is consumed by S3 / S7 / S8; we verify shape, immutability,
and the default `allow_all_budget` hook behavior.
"""
from __future__ import annotations

import dataclasses

import pytest

from router.types import (
    BudgetContext,
    BudgetDecision,
    allow_all_budget,
)


def test_budget_context_is_frozen():
    ctx = BudgetContext(route="cloud", model="qwen3.6-plus")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.route = "local"  # type: ignore[misc]


def test_budget_context_accepts_local_and_cloud():
    BudgetContext(route="local", model="gemma4:e4b")
    BudgetContext(route="cloud", model="qwen3.6-plus")


def test_budget_decision_default_reason_none():
    d = BudgetDecision(allow=True)
    assert d.allow is True
    assert d.reason is None


def test_budget_decision_holds_reason():
    d = BudgetDecision(allow=False, reason="budget exhausted")
    assert d.allow is False
    assert d.reason == "budget exhausted"


@pytest.mark.asyncio
async def test_allow_all_returns_allow_true():
    d = await allow_all_budget(BudgetContext(route="cloud", model="x"))
    assert d.allow is True
    assert d.reason is None
