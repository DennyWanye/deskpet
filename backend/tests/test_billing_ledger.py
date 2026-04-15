"""P2-1-S8 BillingLedger unit tests."""
from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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
    # percent_used contract: 0..100 (see tauri-app/src/types/messages.ts)
    assert s["percent_used"] == pytest.approx(s["spent_today_cny"] / 10.0 * 100.0)


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


# ---------------------------------------------------------------------------
# P2-1-S8 review — daily rollover honors configured tz, not UTC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_day_boundary_uses_configured_tz(monkeypatch):
    """At 02:00 Asia/Shanghai the UTC date is still 'yesterday'; the
    ledger must record today in local time so the daily budget rolls over
    on the Chinese midnight users expect, not the UTC midnight they don't.
    """
    tz = ZoneInfo("Asia/Shanghai")

    # 2026-04-15 02:00 Shanghai == 2026-04-14 18:00 UTC. A UTC-based
    # rollover would bucket this call into 2026-04-14; the local-tz path
    # buckets it into 2026-04-15.
    fixed_local = datetime(2026, 4, 15, 2, 0, tzinfo=tz)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz_arg=None):  # type: ignore[override]
            return fixed_local.astimezone(tz_arg) if tz_arg else fixed_local

    monkeypatch.setattr("billing.ledger.datetime", _FixedDatetime)

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "billing.db"
        l = BillingLedger(
            db_path=db,
            pricing={"qwen3.6-plus": 8.0},
            unknown_model_price_cny_per_m_tokens=20.0,
            daily_budget_cny=10.0,
            tz=tz,
        )
        await l.init()
        await l.record(
            provider="cloud", model="qwen3.6-plus",
            prompt_tokens=1000, completion_tokens=500,
        )
        # spent_today reads the Shanghai-local date, so the row we just
        # wrote counts toward "today" rather than being stranded on
        # yesterday's UTC partition.
        spent = await l.spent_today_cny()
        assert spent > 0
        s = await l.status()
        assert s["tz"] == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_hook_denies_cloud_when_budget_is_zero():
    """Nit: daily_budget_cny=0 must deny every cloud call, even at 0 spent.
    Pin this so a future tweak to the gate (e.g. `spent > budget`) can't
    silently open the cloud up on the zero-budget config users pick to
    disable cloud entirely.
    """
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "billing.db"
        l = BillingLedger(
            db_path=db,
            pricing={},
            unknown_model_price_cny_per_m_tokens=20.0,
            daily_budget_cny=0.0,
        )
        await l.init()
        hook = l.create_hook()
        decision = await hook(BudgetContext(route="cloud", model="any"))
        assert decision.allow is False
        # Local is still free even when cloud is hard-disabled.
        decision_local = await hook(BudgetContext(route="local", model="any"))
        assert decision_local.allow is True
