# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

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


# ----------------------------------------------------------------------
# WI-04 (beta-100) — 80% budget early-warning
# ----------------------------------------------------------------------


@pytest_asyncio.fixture
async def small_budget_ledger():
    """Ledger with a tiny 1.0 CNY budget so a couple of records cross 80%."""
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "billing.db"
        # price 1.0 CNY / 1M tokens → cost = total_tokens / 1e6.
        # 500K tokens = 0.5 CNY = 50% of the 1.0 budget. Clean math.
        l = BillingLedger(
            db_path=db,
            pricing={"m": 1.0},
            unknown_model_price_cny_per_m_tokens=1.0,
            daily_budget_cny=1.0,
        )
        await l.init()
        yield l


@pytest.mark.asyncio
async def test_budget_warning_under_threshold_no_warn(small_budget_ledger):
    # 0.5 CNY spent = 50% of 1.0 budget → no warning
    await small_budget_ledger.record("cloud", "m", 250_000, 250_000)
    r = await small_budget_ledger.check_budget_warning(today_override="2026-05-22")
    assert r["warn"] is False
    assert 49 < r["percent_used"] < 51


@pytest.mark.asyncio
async def test_budget_warning_crosses_80_warns_once(small_budget_ledger):
    # 0.85 CNY = 85% → first call warns
    await small_budget_ledger.record("cloud", "m", 500_000, 350_000)
    r1 = await small_budget_ledger.check_budget_warning(today_override="2026-05-22")
    assert r1["warn"] is True
    assert r1["percent_used"] >= 80.0
    # Same day, still over 80% → must NOT warn again
    r2 = await small_budget_ledger.check_budget_warning(today_override="2026-05-22")
    assert r2["warn"] is False
    # Next day → warns again
    r3 = await small_budget_ledger.check_budget_warning(today_override="2026-05-23")
    assert r3["warn"] is True


@pytest.mark.asyncio
async def test_budget_warning_zero_budget_never_warns(ledger):
    """daily_budget_cny<=0 means unlimited — never warn, but still report."""
    zero = BillingLedger(
        db_path=ledger._db_path,
        pricing={},
        unknown_model_price_cny_per_m_tokens=20.0,
        daily_budget_cny=0.0,
    )
    await zero.init()
    await zero.record("cloud", "anything", 1_000_000, 1_000_000)
    r = await zero.check_budget_warning(today_override="2026-05-22")
    assert r["warn"] is False
    assert r["spent_today_cny"] > 0  # running total still reported


@pytest.mark.asyncio
async def test_budget_warning_exact_80_boundary(small_budget_ledger):
    # Exactly 0.80 CNY = 80.0% → boundary is inclusive (>=)
    await small_budget_ledger.record("cloud", "m", 400_000, 400_000)
    r = await small_budget_ledger.check_budget_warning(today_override="2026-05-22")
    assert r["percent_used"] == pytest.approx(80.0)
    assert r["warn"] is True


@pytest.mark.asyncio
async def test_budget_warning_fields_present(small_budget_ledger):
    r = await small_budget_ledger.check_budget_warning(today_override="2026-05-22")
    for key in ("warn", "spent_today_cny", "daily_budget_cny", "percent_used", "message"):
        assert key in r


@pytest.mark.asyncio
async def test_t6_4_budget_warning_message_has_recharge_hint(small_budget_ledger):
    """WI-R5 / T6-4 — the 80% warning text carries a recharge link so the
    user can top up at the relay console in one click."""
    from billing.ledger import RECHARGE_HINT_URL

    r = await small_budget_ledger.check_budget_warning(today_override="2026-05-22")
    assert RECHARGE_HINT_URL in r["message"]
    assert "充值" in r["message"]
