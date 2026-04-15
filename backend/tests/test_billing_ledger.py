"""P2-1-S8 BillingLedger unit tests."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from billing.ledger import BillingLedger
from router.types import BudgetContext


@pytest_asyncio.fixture
async def ledger():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "billing.db"
        l = BillingLedger(
            db_path=db,
            pricing={"qwen3.6-plus": 8.0, "deepseek-chat": 1.0},
            unknown_model_price_cny_per_m_tokens=20.0,
            daily_budget_cny=10.0,
        )
        await l.init()
        yield l


@pytest.mark.asyncio
async def test_record_then_spent_today(ledger):
    await ledger.record(
        provider="cloud",
        model="qwen3.6-plus",
        prompt_tokens=1000,
        completion_tokens=500,
    )
    # (1000+500)/1_000_000 * 8.0 = 0.012
    spent = await ledger.spent_today_cny()
    assert abs(spent - 0.012) < 1e-6


@pytest.mark.asyncio
async def test_unknown_model_uses_fallback_price(ledger):
    await ledger.record(
        provider="cloud",
        model="some-new-model",
        prompt_tokens=500_000,
        completion_tokens=500_000,
    )
    # 1.0M tokens * 20.0 = 20.0
    spent = await ledger.spent_today_cny()
    assert abs(spent - 20.0) < 1e-6


@pytest.mark.asyncio
async def test_local_provider_records_zero_cost(ledger):
    await ledger.record(
        provider="local",
        model="qwen3:4b",
        prompt_tokens=1000,
        completion_tokens=500,
    )
    spent = await ledger.spent_today_cny()
    assert spent == 0.0


@pytest.mark.asyncio
async def test_status_reports_remaining(ledger):
    await ledger.record("cloud", "qwen3.6-plus", 1000, 500)
    s = await ledger.status()
    assert s["spent_today_cny"] > 0
    assert s["daily_budget_cny"] == 10.0
    assert s["remaining_cny"] == pytest.approx(10.0 - s["spent_today_cny"])
    assert s["percent_used"] == pytest.approx(s["spent_today_cny"] / 10.0)


@pytest.mark.asyncio
async def test_hook_allows_local_route_always(ledger):
    # Even if already over budget, local is free and always allowed.
    await ledger.record("cloud", "qwen3.6-plus", 500_000, 500_000)  # 8.0 cny
    hook = ledger.create_hook()
    decision = await hook(BudgetContext(route="local", model="qwen3:4b"))
    assert decision.allow is True


@pytest.mark.asyncio
async def test_hook_denies_cloud_when_over_budget(ledger):
    # daily_budget=10.0; record 20cny worth → over.
    await ledger.record("cloud", "some-unknown", 500_000, 500_000)  # 20cny
    hook = ledger.create_hook()
    decision = await hook(BudgetContext(route="cloud", model="qwen3.6-plus"))
    assert decision.allow is False
    assert "daily_budget_exceeded" in (decision.reason or "")


@pytest.mark.asyncio
async def test_hook_allows_cloud_when_under_budget(ledger):
    await ledger.record("cloud", "qwen3.6-plus", 1000, 500)  # 0.012cny
    hook = ledger.create_hook()
    decision = await hook(BudgetContext(route="cloud", model="qwen3.6-plus"))
    assert decision.allow is True
