"""Shared types for router-level budget hook (P2-1-S6 skeleton).

`BudgetHook` is the async contract HybridRouter calls before delegating to
a provider. S6 ships only the type + `allow_all_budget` default; S8 will
ship a `BillingLedger` that produces a real hook (debit cloud spend,
block when budget is exhausted).

The signature below is the cross-slice contract — S3 / S7 / S8 all import
these names verbatim. See:
  docs/superpowers/specs/2026-04-15-p2-1-finale-design.md §1.1, §2.2, §3
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal


@dataclass(frozen=True)
class BudgetContext:
    """Context passed to a BudgetHook before dispatching a chat turn.

    - ``route``: which provider HybridRouter is about to call.
    - ``model``: the model id HybridRouter will request from that provider.
    """
    route: Literal["local", "cloud"]
    model: str


@dataclass(frozen=True)
class BudgetDecision:
    """Outcome of a BudgetHook call.

    - ``allow``: when False, HybridRouter must skip the attempted route.
      Caller is responsible for deciding fallback (e.g., cloud→local).
    - ``reason``: human-readable; UI may surface as a toast when deny.
    """
    allow: bool
    reason: str | None = None


BudgetHook = Callable[[BudgetContext], Awaitable[BudgetDecision]]
"""Async hook signature. ``async def hook(ctx) -> BudgetDecision``."""


async def allow_all_budget(ctx: BudgetContext) -> BudgetDecision:
    """Default no-op hook used when no ledger is wired.

    HybridRouter's default ``budget_hook=allow_all_budget`` preserves the
    pre-S6 behavior (no gating). S8 swaps in a real hook backed by
    BillingLedger without any signature change.
    """
    return BudgetDecision(allow=True, reason=None)
