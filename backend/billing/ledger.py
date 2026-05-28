# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P2-1-S8 BillingLedger — SQLite per-call usage & cost recording.

Data model: single `calls` table, one row per chat_stream completion.
Cost model: cloud provider → price from pricing table (per-1M-token, prompt+
completion combined), unknown models use a configured fallback price.
Local provider → cost=0 regardless of tokens.

DailyBudgetHook contract: see docs/superpowers/specs/2026-04-15-p2-1-finale-design.md §1.1.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import aiosqlite
import structlog

from router.types import BudgetContext, BudgetDecision, BudgetHook

logger = structlog.get_logger()

# Nit (P2-1-S8 review): lift the magic string so hook / record / cost
# logic all agree on what "local means zero-cost, skip the budget gate".
_LOCAL_ROUTE = "local"

# WI-R5 (beta-100 relay): the 80%-budget warning embeds a recharge hint
# pointing at the 中转站 console. Kept here so the message stays in one
# place; the frontend renders it verbatim.
RECHARGE_HINT_URL = "https://chinzy.com/console/billing"

# P2-1-S8 review: daily rollover defaults to Asia/Shanghai because the
# product targets Chinese users — at 02:00 Beijing the UTC `.date()` is
# still "yesterday", so a fresh-day budget reset would be off by a day
# during the active-user window. Can be overridden via BillingConfig.tz.
_DEFAULT_TZ = ZoneInfo("Asia/Shanghai")


CREATE_SQL = """
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    ts_date TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    cost_cny REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS calls_ts_date_idx ON calls(ts_date);
"""


class BillingLedger:
    """Per-call usage record + daily budget query.

    Write path is serialized via an asyncio.Lock so concurrent chat_stream
    completions can't step on each other in the SQLite WAL.
    """

    def __init__(
        self,
        *,
        db_path: Path,
        pricing: dict[str, float],
        unknown_model_price_cny_per_m_tokens: float,
        daily_budget_cny: float,
        tz: ZoneInfo = _DEFAULT_TZ,
    ) -> None:
        self._db_path = db_path
        self._pricing = pricing
        self._unknown_price = unknown_model_price_cny_per_m_tokens
        self._daily_budget = daily_budget_cny
        self._tz = tz
        self._lock = asyncio.Lock()
        # WI-04 (beta-100): the 80%-budget early-warning is fired at most
        # once per local day. We remember the last day we warned in-process
        # — a backend restart resets it, which is acceptable (re-warning
        # once after a restart is harmless, and never warning is worse).
        self._last_warned_date: str | None = None

    async def init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(CREATE_SQL)
            await db.commit()

    def _cost_cny(self, provider: str, model: str, total_tokens: int) -> float:
        if provider == _LOCAL_ROUTE:
            return 0.0
        price = self._pricing.get(model, self._unknown_price)
        return total_tokens / 1_000_000.0 * price

    async def record(
        self,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        total = prompt_tokens + completion_tokens
        cost = self._cost_cny(provider, model, total)
        # ts_utc keeps the canonical UTC wall clock for auditing; ts_date is
        # the *local* day key used by the daily rollover (see _DEFAULT_TZ).
        now_utc = datetime.now(timezone.utc)
        today_local = datetime.now(self._tz).date().isoformat()
        async with self._lock:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    "INSERT INTO calls (ts_utc, ts_date, provider, model, "
                    "prompt_tokens, completion_tokens, cost_cny) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        now_utc.isoformat(),
                        today_local,
                        provider,
                        model,
                        prompt_tokens,
                        completion_tokens,
                        cost,
                    ),
                )
                await db.commit()
        logger.info(
            "billing_record",
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_cny=cost,
        )

    async def spent_today_cny(self) -> float:
        today = datetime.now(self._tz).date().isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT COALESCE(SUM(cost_cny), 0.0) FROM calls WHERE ts_date = ?",
                (today,),
            ) as cur:
                row = await cur.fetchone()
                return float(row[0]) if row else 0.0

    async def status(self) -> dict:
        spent = await self.spent_today_cny()
        remaining = max(0.0, self._daily_budget - spent)
        # UI contract (tauri-app/src/types/messages.ts): percent_used is
        # 0..100. Keep the division-by-zero guard returning 100 (fully
        # consumed) — matches `daily_budget_cny = 0.0` meaning "always
        # denied".
        pct = (
            (spent / self._daily_budget) * 100.0
            if self._daily_budget > 0
            else 100.0
        )
        return {
            "spent_today_cny": spent,
            "daily_budget_cny": self._daily_budget,
            "remaining_cny": remaining,
            "percent_used": pct,
            # Exposed so the frontend can show which clock the rollover
            # uses (debugging a disputed "why is my budget still X?" trip).
            "tz": str(self._tz),
        }

    # WI-04 budget-warning threshold: warn the user once they cross this
    # fraction of the daily budget, so they're not blindsided by the hard
    # 100% block (which `create_hook` enforces).
    WARN_THRESHOLD_PCT: float = 80.0

    async def check_budget_warning(
        self, *, today_override: str | None = None,
    ) -> dict:
        """WI-04 — decide whether to fire the 80%-budget early warning.

        Returns ``{"warn", "spent_today_cny", "daily_budget_cny",
        "percent_used"}``. ``warn`` is True at most **once per local
        day**: the first call that observes ``percent_used >= 80`` flips
        it True and records the day; subsequent same-day calls return
        False even while still over 80%.

        Contract notes:
          * ``daily_budget_cny <= 0`` (unlimited / unconfigured) →
            ``warn`` is always False; we still report ``spent`` so the
            settings panel can show the running total.
          * The hard 100% block is a *separate* mechanism — see
            :meth:`create_hook`. This method never blocks anything.
          * ``today_override`` lets tests pin the local day without
            monkey-patching the clock.
        """
        spent = await self.spent_today_cny()
        budget = self._daily_budget
        if budget <= 0:
            return {
                "warn": False,
                "spent_today_cny": spent,
                "daily_budget_cny": budget,
                "percent_used": 0.0,
            }
        pct = (spent / budget) * 100.0
        today = today_override or datetime.now(self._tz).date().isoformat()
        warn = pct >= self.WARN_THRESHOLD_PCT and self._last_warned_date != today
        if warn:
            self._last_warned_date = today
        # WI-R5: the warning text carries a recharge hint so the user has
        # a one-click path to top up at the relay console.
        message = (
            f"今日已用 ¥{spent:.2f} / ¥{budget:.2f}（{pct:.0f}%）。"
            f"余额不足可前往中转站充值：{RECHARGE_HINT_URL}"
        )
        return {
            "warn": warn,
            "spent_today_cny": spent,
            "daily_budget_cny": budget,
            "percent_used": pct,
            "message": message,
        }

    def create_hook(self) -> BudgetHook:
        """Returns a BudgetHook ready for HybridRouter injection.

        Semantics:
          - local route → always allow (local is free)
          - cloud route → deny once spent_today >= daily_budget_cny
        """
        async def _hook(ctx: BudgetContext) -> BudgetDecision:
            if ctx.route == _LOCAL_ROUTE:
                return BudgetDecision(allow=True)
            spent = await self.spent_today_cny()
            if spent >= self._daily_budget:
                return BudgetDecision(
                    allow=False,
                    reason=(
                        f"daily_budget_exceeded:"
                        f"{spent:.3f}/{self._daily_budget:.3f}"
                    ),
                )
            return BudgetDecision(allow=True)

        return _hook
