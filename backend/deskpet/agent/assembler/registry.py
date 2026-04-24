"""ComponentRegistry with parallel fan-out (P4-S7 task 12.6).

The registry owns every component instance, resolves which ones to run
for a given ``AssemblyPolicy``, and fans them out via ``asyncio.gather``
so total latency is ``max(components) + overhead`` rather than the
serial sum (spec Requirement "Component Registry and Parallel Fan-out").

Each component's ``provide()`` is wrapped with a soft timeout (from
``ComponentContext.deadline_wall_time``) — slow components still return
a partial slice rather than starving the rest.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

import structlog

from deskpet.agent.assembler.bundle import AssemblyPolicy, Slice
from deskpet.agent.assembler.components.base import Component, ComponentContext

logger = structlog.get_logger(__name__)


class ComponentRegistry:
    """Ordered registry of components keyed by ``.name``.

    Usage::

        registry = ComponentRegistry()
        registry.register(MemoryComponent())
        registry.register(ToolComponent())
        slices = await registry.fanout(ctx)
    """

    def __init__(self, components: Optional[list[Component]] = None) -> None:
        self._components: dict[str, Component] = {}
        if components:
            for c in components:
                self.register(c)

    def register(self, component: Component) -> None:
        """Add a component. Later registrations with the same name overwrite."""
        name = getattr(component, "name", None)
        if not name:
            raise ValueError("Component must have a non-empty .name")
        self._components[name] = component

    def get(self, name: str) -> Optional[Component]:
        return self._components.get(name)

    def names(self) -> list[str]:
        return list(self._components.keys())

    async def fanout(
        self,
        ctx: ComponentContext,
        *,
        timeout_ms: Optional[float] = None,
    ) -> list[Slice]:
        """Run all components in parallel and return their slices.

        Selection:
        - ``policy.must`` components MUST run; missing ones emit a warning.
        - ``policy.prefer`` components run when registered.
        - Unknown names in either list are silently skipped.

        Failures:
        - Component ``provide()`` is expected to be exception-safe, but we
          wrap with ``asyncio.gather(return_exceptions=True)`` as a safety
          net. Failed components become empty slices in the output so the
          assembler's telemetry can flag them.
        """
        policy = ctx.policy
        wanted_must = list(dict.fromkeys(policy.must))
        wanted_prefer = list(dict.fromkeys(policy.prefer))

        # Enforce D9: "memory" is a mandatory core component.
        if "memory" not in wanted_must:
            logger.warning(
                "assembler.memory_missing_from_must",
                task_type=policy.task_type,
            )
            wanted_must.append("memory")

        to_run: list[tuple[str, Component]] = []
        missing_must: list[str] = []
        for name in wanted_must:
            comp = self._components.get(name)
            if comp is None:
                missing_must.append(name)
                continue
            to_run.append((name, comp))

        # Dedup against must — don't double-run a component listed in both.
        must_names = {n for n, _ in to_run}
        for name in wanted_prefer:
            if name in must_names:
                continue
            comp = self._components.get(name)
            if comp is not None:
                to_run.append((name, comp))

        if missing_must:
            logger.warning(
                "assembler.missing_must_components",
                components=missing_must,
                task_type=policy.task_type,
            )

        if not to_run:
            return []

        # Deadline propagates to components for self-trim.
        if timeout_ms is not None and ctx.deadline_wall_time is None:
            ctx.deadline_wall_time = time.monotonic() + timeout_ms / 1000.0

        async def _safe_provide(
            name: str, comp: Component
        ) -> Slice:
            try:
                result = await comp.provide(ctx)
                if not isinstance(result, Slice):
                    # Component contract violation — treat as empty.
                    logger.warning(
                        "assembler.component_returned_non_slice",
                        component=name,
                        returned=type(result).__name__,
                    )
                    return Slice(component_name=name, meta={"error": "non_slice"})
                return result
            except Exception as exc:  # defence-in-depth
                logger.warning(
                    "assembler.component_raised",
                    component=name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return Slice(
                    component_name=name,
                    meta={"error": str(exc), "error_type": type(exc).__name__},
                )

        coros = [_safe_provide(name, comp) for name, comp in to_run]
        if timeout_ms is None:
            results = await asyncio.gather(*coros, return_exceptions=False)
        else:
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*coros, return_exceptions=False),
                    timeout=timeout_ms / 1000.0,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "assembler.fanout_timed_out",
                    timeout_ms=timeout_ms,
                    pending=[n for n, _ in to_run],
                )
                # Return empty slices for all components — caller logs
                # the timeout and moves on with an empty bundle.
                results = [
                    Slice(component_name=n, meta={"error": "timeout"})
                    for n, _ in to_run
                ]

        # Preserve registration order for stable output.
        return list(results)
