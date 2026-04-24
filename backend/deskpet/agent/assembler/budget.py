"""BudgetAllocator — trim context when over budget (P4-S7 task 12.9).

Called after the :class:`ComponentRegistry` has fanned out and collected
slices. Computes the total estimated token count; if it exceeds the
budget (``context_window * budget_ratio``, default 0.6), it trims the
slices in ascending ``priority`` order — cheapest components first — until
the budget is met or only core slices remain.

Trimming strategy:

1. Drop slices with ``priority <= 20`` (time, workspace) whole.
2. Shrink ``memory`` slice by re-packaging only its first N characters,
   proxying "cut L3 top_k". We don't re-call the memory manager here —
   we just truncate text content and note the cut in ``decisions.budget_cut``.
3. Memory priority 100 is NEVER dropped completely (D9).

Output: the same slice list but each slice's ``text_content`` and
``tokens`` are updated to reflect the trim.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import structlog

from deskpet.agent.assembler.bundle import Slice

logger = structlog.get_logger(__name__)


@dataclass
class BudgetResult:
    """Outcome of :meth:`BudgetAllocator.allocate`."""

    slices: list[Slice]
    cut: list[str] = field(default_factory=list)
    total_tokens: int = 0
    budget_tokens: int = 0


class BudgetAllocator:
    """Trim slices to fit a token budget.

    Parameters
    ----------
    context_window:
        Model's context window (tokens). Default 200_000 (Claude 4.5).
    budget_ratio:
        Fraction of the window reserved for context. Default 0.6 — leaves
        room for the conversation itself + assistant response.
    min_memory_tokens:
        Floor for the memory slice when shrinking. Default 300 — below
        this the bundle is useless so we stop trimming and emit a
        warning instead.
    """

    def __init__(
        self,
        *,
        context_window: int = 200_000,
        budget_ratio: float = 0.6,
        min_memory_tokens: int = 300,
    ) -> None:
        self.context_window = context_window
        self.budget_ratio = budget_ratio
        self.min_memory_tokens = min_memory_tokens

    def budget_tokens(self, *, override_ratio: Optional[float] = None) -> int:
        ratio = override_ratio if override_ratio is not None else self.budget_ratio
        return max(0, int(self.context_window * ratio))

    def allocate(
        self,
        slices: list[Slice],
        *,
        override_ratio: Optional[float] = None,
    ) -> BudgetResult:
        budget = self.budget_tokens(override_ratio=override_ratio)
        total = sum(max(0, s.tokens) for s in slices)

        if total <= budget:
            return BudgetResult(
                slices=list(slices), cut=[], total_tokens=total, budget_tokens=budget
            )

        # Sort a copy: trim low-priority first.
        trim_order = sorted(
            range(len(slices)), key=lambda i: slices[i].priority
        )
        cut: list[str] = []
        working = list(slices)

        for idx in trim_order:
            sl = working[idx]
            # Memory (priority 100) is special — can only shrink, never drop.
            if sl.component_name == "memory":
                continue
            # Everything else below the "always keep" line → drop whole.
            if sl.priority < 80:
                if sl.tokens > 0:
                    total -= sl.tokens
                    cut.append(sl.component_name)
                working[idx] = _zero_out(sl)
                if total <= budget:
                    break

        if total > budget:
            # Need to shrink memory. Keep its proportional share of the
            # remaining budget, but never below min_memory_tokens.
            for idx in trim_order:
                if working[idx].component_name == "memory":
                    mem = working[idx]
                    keep = max(
                        self.min_memory_tokens,
                        budget - (total - mem.tokens),
                    )
                    if keep < mem.tokens:
                        shrunk = _shrink_slice(mem, keep)
                        total -= mem.tokens - shrunk.tokens
                        working[idx] = shrunk
                        cut.append("memory_shrink")
                    break

        if total > budget:
            logger.warning(
                "assembler.budget_unmet",
                total_tokens=total,
                budget_tokens=budget,
                cut=cut,
            )

        return BudgetResult(
            slices=working,
            cut=cut,
            total_tokens=total,
            budget_tokens=budget,
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------
def _zero_out(sl: Slice) -> Slice:
    """Return a copy of ``sl`` with its text content + tokens zeroed.

    ``tool_schemas`` is preserved — dropping a tool component's text
    shouldn't strip the schemas (they live on the bundle separately).
    For tool slices specifically we'd usually leave them alone because
    their text is empty anyway.
    """
    return Slice(
        component_name=sl.component_name,
        text_content="",
        tool_schemas=list(sl.tool_schemas),
        tokens=0,
        priority=sl.priority,
        bucket=sl.bucket,
        meta={**sl.meta, "trimmed": "dropped"},
    )


_TRIM_MARKER = "\n[…trimmed by budget]"
_TRIM_MARKER_TOKENS = max(1, len(_TRIM_MARKER) // 4)


def _shrink_slice(sl: Slice, target_tokens: int) -> Slice:
    """Truncate a slice's text to roughly ``target_tokens`` tokens.

    Token estimate here is the same coarse "1 token ≈ 4 chars" used
    elsewhere. We reserve room for the trim marker itself so the final
    slice never exceeds ``target_tokens``.
    """
    if target_tokens <= 0 or not sl.text_content:
        return _zero_out(sl)
    # Reserve tokens for the trim marker + safety slack.
    body_tokens = max(1, target_tokens - _TRIM_MARKER_TOKENS - 1)
    chars = max(4, body_tokens * 4)
    if len(sl.text_content) <= chars:
        return sl
    truncated = sl.text_content[:chars].rstrip() + _TRIM_MARKER
    # Recompute from actual truncated length — keep the post-condition
    # ``new.tokens <= target_tokens`` tight.
    new_tokens = min(target_tokens, max(1, len(truncated) // 4))
    return Slice(
        component_name=sl.component_name,
        text_content=truncated,
        tool_schemas=list(sl.tool_schemas),
        tokens=new_tokens,
        priority=sl.priority,
        bucket=sl.bucket,
        meta={**sl.meta, "trimmed": "shrunk"},
    )
