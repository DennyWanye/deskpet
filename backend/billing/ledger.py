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

import aiosqlite
import structlog

from router.types import BudgetContext, BudgetDecision, BudgetHook

logger = structlog.get_logger()


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
    ) -> None:
        self._db_path = db_path
        self._pricing = pricing
        self._unknown_price = unknown_model_price_cny_per_m_tokens
        self._daily_budget = daily_budget_cny
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(CREATE_SQL)
            await db.commit()

    def _cost_cny(self, provider: str, model: str, total_tokens: int) -> float:
        if provider == "local":
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
        now = datetime.now(timezone.utc)
        async with self._lock:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    "INSERT INTO calls (ts_utc, ts_date, provider, model, "
                    "prompt_tokens, completion_tokens, cost_cny) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        now.isoformat(),
                        now.date().isoformat(),
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
        today = datetime.now(timezone.utc).date().isoformat()
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
        pct = spent / self._daily_budget if self._daily_budget > 0 else 1.0
        return {
            "spent_today_cny": spent,
            "daily_budget_cny": self._daily_budget,
            "remaining_cny": remaining,
            "percent_used": pct,
        }

    def create_hook(self) -> BudgetHook:
        """Returns a BudgetHook ready for HybridRouter injection.

        Semantics:
          - local route → always allow (local is free)
          - cloud route → deny once spent_today >= daily_budget_cny
        """
        async def _hook(ctx: BudgetContext) -> BudgetDecision:
            if ctx.route == "local":
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
