"""Skill component (P4-S7 task 12.5).

Emits a short "skill prelude" listing skills that are currently applicable
to the task. Real skill registry lands in P4-S10; for now this component
is None-safe and returns an empty slice when no registry is injected.

Output goes into the ``"skill"`` bucket (``skill_prelude`` on the bundle)
which sits between ``frozen_system`` and ``memory_block``.
"""
from __future__ import annotations

import time
from typing import Any

from deskpet.agent.assembler.bundle import Slice
from deskpet.agent.assembler.components.base import Component, ComponentContext


class SkillComponent:
    """Emits a skill prelude block when skills are registered."""

    name: str = "skill"

    async def provide(self, ctx: ComponentContext) -> Slice:
        start = time.monotonic()
        registry = ctx.skill_registry
        if registry is None:
            return Slice(
                component_name=self.name,
                priority=70,
                bucket="skill",
                meta={"status": "no_registry"},
            )

        # Expect a duck-typed ``select(task_type, prefer)`` method or
        # ``all()`` if the registry is too simple to filter. Either is
        # fine — the component stays soft-failure on any shape drift.
        skills: list[Any]
        try:
            if hasattr(registry, "select"):
                maybe = registry.select(
                    ctx.task_type, prefer=list(ctx.policy.prefer)
                )
                skills = await maybe if hasattr(maybe, "__await__") else maybe
            elif hasattr(registry, "all"):
                maybe = registry.all()
                skills = await maybe if hasattr(maybe, "__await__") else maybe
            else:
                skills = []
        except Exception as exc:
            return Slice(
                component_name=self.name,
                priority=70,
                bucket="skill",
                meta={"error": str(exc), "error_type": type(exc).__name__},
            )

        if not skills:
            return Slice(
                component_name=self.name,
                priority=70,
                bucket="skill",
                meta={"count": 0},
            )

        lines = ["## 可用技能"]
        for s in skills:
            name = _skill_attr(s, "name", "?")
            summary = _skill_attr(s, "summary", "") or _skill_attr(s, "description", "")
            if summary:
                lines.append(f"- **{name}**: {summary}")
            else:
                lines.append(f"- **{name}**")

        text = "\n".join(lines)
        elapsed_ms = (time.monotonic() - start) * 1000.0
        return Slice(
            component_name=self.name,
            text_content=text,
            tokens=max(1, len(text) // 4),
            priority=70,
            bucket="skill",
            meta={"count": len(skills), "latency_ms": round(elapsed_ms, 2)},
        )


def _skill_attr(s: Any, attr: str, default: Any = None) -> Any:
    if isinstance(s, dict):
        return s.get(attr, default)
    return getattr(s, attr, default)


_ASSERT_PROTOCOL: Component = SkillComponent()
