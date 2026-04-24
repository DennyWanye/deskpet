"""Tool component (P4-S7 task 12.5).

Filters :class:`~deskpet.tools.registry.ToolRegistry`'s schema list down
to the whitelist in ``policy.tools``. The component does NOT emit text;
its contribution is purely the ``tool_schemas`` list, which the assembler
merges into ``ContextBundle.tool_schemas``.

Whitelist semantics (spec Requirement "Declarative YAML Assembly Policy"):

- ``policy.tools == ["*"]``      → all tools exposed
- ``policy.tools == []``         → no tools this turn
- ``policy.tools == ["a", "b"]`` → only "a" and "b" (if present)

Unknown tools in the whitelist are silently skipped — helps hot-reload
when the registry hasn't caught up to a new policy yet.
"""
from __future__ import annotations

import time
from typing import Any

from deskpet.agent.assembler.bundle import Slice
from deskpet.agent.assembler.components.base import Component, ComponentContext


class ToolComponent:
    """Filters the tool registry to the policy whitelist."""

    name: str = "tool"

    async def provide(self, ctx: ComponentContext) -> Slice:
        start = time.monotonic()
        registry = ctx.tool_registry
        whitelist = list(ctx.policy.tools)

        # No registry wired — empty slice.
        if registry is None:
            return Slice(
                component_name=self.name,
                priority=60,
                bucket="frozen",
                meta={"status": "no_registry"},
            )

        # Explicit "no tools"
        if whitelist == []:
            return Slice(
                component_name=self.name,
                priority=60,
                bucket="frozen",
                meta={"filtered": 0, "requested": 0},
            )

        try:
            all_schemas = registry.schemas()
        except Exception as exc:  # defensive
            return Slice(
                component_name=self.name,
                priority=60,
                bucket="frozen",
                meta={"error": str(exc), "error_type": type(exc).__name__},
            )

        if whitelist == ["*"]:
            filtered = list(all_schemas or [])
        else:
            wanted = set(whitelist)
            filtered = [
                s for s in (all_schemas or []) if _schema_name(s) in wanted
            ]

        elapsed_ms = (time.monotonic() - start) * 1000.0
        return Slice(
            component_name=self.name,
            priority=60,
            bucket="frozen",
            tool_schemas=filtered,
            meta={
                "filtered": len(filtered),
                "requested": len(whitelist) if whitelist != ["*"] else "*",
                "latency_ms": round(elapsed_ms, 2),
            },
        )


def _schema_name(schema: dict[str, Any]) -> str:
    """Pull the tool name from an OpenAI-format function schema.

    Accepts both the nested ``{"type": "function", "function": {"name": "..."}}``
    shape and a flat ``{"name": "..."}`` shape used by some registries.
    """
    if not isinstance(schema, dict):
        return ""
    if "function" in schema and isinstance(schema["function"], dict):
        return str(schema["function"].get("name", ""))
    return str(schema.get("name", ""))


_ASSERT_PROTOCOL: Component = ToolComponent()
