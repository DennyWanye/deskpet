"""Router cross-slice type contracts (P2-1 finale spec §1.1 / §3).

These dataclasses are the stable public surface between:
  - HybridRouter (S2, consumed by S8)
  - BillingLedger / BudgetHook (S8)
  - Future strategies (P2-2+)

This module intentionally has **no runtime dependencies** other than stdlib,
so any slice can import from it without pulling in heavy providers.

Shipped in S8 as a stub so the S8 worktree can compile on its own; S6 will
land the identical signature on master. The merge is clean because both
slices agree on this exact shape (see spec §3 "cross-slice contracts").
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal


@dataclass(frozen=True)
class BudgetContext:
    """Context passed to a BudgetHook before each provider call."""
    route: Literal["local", "cloud"]
    model: str


@dataclass(frozen=True)
class BudgetDecision:
    """Hook verdict. allow=False → HybridRouter raises LLMUnavailableError."""
    allow: bool
    reason: str | None = None


BudgetHook = Callable[[BudgetContext], Awaitable["BudgetDecision"]]


async def allow_all_budget(ctx: BudgetContext) -> BudgetDecision:
    """Default hook: never denies. HybridRouter's default when caller omits."""
    return BudgetDecision(allow=True)
