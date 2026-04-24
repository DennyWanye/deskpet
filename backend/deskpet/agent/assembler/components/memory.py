"""Memory component (P4-S7 task 12.5).

Wraps the three-layer :class:`~deskpet.memory.manager.MemoryManager`. On
every turn this component asks the manager for:

1. L1 frozen snapshot (`{memory, user}` raw file text) — optional.
2. L2 recent session messages.
3. L3 RRF hybrid recall hits.

It then packages the results into a single markdown-ish block that the
LLM can consume naturally. L1 is treated as ``"frozen"`` bucket (it only
changes when the user edits their MEMORY.md / USER.md — cache-friendly),
while L2 + L3 go into ``"dynamic"`` because they shift every turn.

Core memory (L1) MAY NEVER be removed by policy (spec D9). The component
honours ``policy.memory.l1 == "off"`` by skipping the *block* but still
adds L1 content to the frozen bucket — just with empty strings.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from deskpet.agent.assembler.bundle import Slice
from deskpet.agent.assembler.components.base import Component, ComponentContext


class MemoryComponent:
    """Provides L1 snapshot + L2/L3 recall results."""

    name: str = "memory"

    async def provide(self, ctx: ComponentContext) -> Slice:
        start = time.monotonic()
        mm = ctx.memory_manager
        if mm is None:
            # No memory manager wired — graceful empty. The assembler
            # never "fails" a turn just because L3 is offline.
            return Slice(
                component_name=self.name,
                text_content="",
                tokens=0,
                priority=100,
                bucket="frozen",
                meta={"status": "no_memory_manager"},
            )

        policy_memory = ctx.policy.memory
        call_policy = {
            "l1": policy_memory.l1,
            "l2_top_k": policy_memory.l2_top_k,
            "l3_top_k": policy_memory.l3_top_k,
            "session_id": ctx.session_id,
        }

        try:
            result = await mm.recall(ctx.user_message, policy=call_policy)
        except Exception as exc:  # defensive — manager is supposed to be safe
            return Slice(
                component_name=self.name,
                text_content="",
                priority=100,
                bucket="frozen",
                meta={"error": str(exc), "error_type": type(exc).__name__},
            )

        frozen_text = _render_l1(result.get("l1"))
        dynamic_text = _render_l2_l3(
            result.get("l2") or [],
            result.get("l3") or [],
        )

        # Frozen slice for L1
        combined = frozen_text
        if dynamic_text:
            # Two sections merged; the frozen half comes first so the
            # whole block stays cache-friendly as long as L1 doesn't
            # change. When L2/L3 shift we only invalidate the tail.
            combined = f"{frozen_text}\n\n{dynamic_text}".strip()

        tokens = _approx_tokens(combined)
        elapsed_ms = (time.monotonic() - start) * 1000.0

        return Slice(
            component_name=self.name,
            text_content=combined,
            tokens=tokens,
            priority=100,
            bucket="dynamic" if dynamic_text else "frozen",
            meta={
                "l1_bytes": len(frozen_text),
                "l2_count": len(result.get("l2") or []),
                "l3_count": len(result.get("l3") or []),
                "latency_ms": round(elapsed_ms, 2),
            },
        )


# ---------------------------------------------------------------------------
# Renderers — keep layout stable for prompt caching
# ---------------------------------------------------------------------------
def _render_l1(l1: Any) -> str:
    """Render L1 snapshot into a stable block.

    Returns empty string if l1 is missing / empty — avoids inserting a
    blank section that would still occupy tokens in the prompt.
    """
    if not isinstance(l1, dict):
        return ""
    memory = (l1.get("memory") or "").strip()
    user = (l1.get("user") or "").strip()
    if not memory and not user:
        return ""

    parts: list[str] = ["## 记忆档案 (L1, frozen)"]
    if memory:
        parts.append("### MEMORY.md\n" + memory)
    if user:
        parts.append("### USER.md\n" + user)
    return "\n\n".join(parts)


def _render_l2_l3(
    l2_rows: list[dict[str, Any]], l3_hits: list[dict[str, Any]]
) -> str:
    """Render L2 (recent session) + L3 (RRF recall) into one dynamic block."""
    parts: list[str] = []

    if l2_rows:
        parts.append("## 近期对话 (L2, recent)")
        lines = []
        for row in l2_rows:
            role = row.get("role", "?")
            content = (row.get("content") or "").strip()
            if not content:
                continue
            # Truncate long messages — keep the recall compact.
            if len(content) > 200:
                content = content[:200] + "…"
            lines.append(f"- [{role}] {content}")
        if lines:
            parts.append("\n".join(lines))
        else:
            parts.pop()  # drop the header we just added

    if l3_hits:
        parts.append("## 相关记忆片段 (L3, RRF recall)")
        lines = []
        for hit in l3_hits:
            text = (hit.get("text") or "").strip()
            if not text:
                continue
            if len(text) > 240:
                text = text[:240] + "…"
            score = hit.get("score")
            src = hit.get("source", "?")
            score_str = (
                f"{score:.3f}" if isinstance(score, (int, float)) else "?"
            )
            lines.append(f"- [{src} {score_str}] {text}")
        if lines:
            parts.append("\n".join(lines))
        else:
            parts.pop()

    return "\n\n".join(parts)


def _approx_tokens(text: str) -> int:
    """Very coarse token estimate (1 token ≈ 4 chars for mixed Chinese+English).

    Accurate enough for budget allocation. The real tokeniser is
    provider-specific; we avoid importing tiktoken here to keep the
    hot path fast.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


# For structural typing / Protocol conformance checking in tests.
_ASSERT_PROTOCOL: Component = MemoryComponent()  # noqa: E501
