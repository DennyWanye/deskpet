# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

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

        # F1 (memory-stage2-followup): per-component 计时 + per-component
        # 软超时。旧实现只有一个整体 wait_for —— 任何一个组件慢（e.g.
        # MemoryComponent 向量召回碰 BGE-M3 冷加载）会让整批 fanout 超时，
        # 所有组件（连 trivial 的 time/persona）一起变空 slice，且 log 只打
        # pending=[全部]，无法定位真凶。现在每个组件独立计时 + 独立软
        # 超时：慢的单独降级，快的照常返回，并把 duration_ms/status 记进
        # meta，使 round-3 那种 `pending=[workspace,...]` 能精确到组件。
        per_component_timeout_s: Optional[float] = None
        if timeout_ms is not None:
            per_component_timeout_s = max(0.05, timeout_ms / 1000.0)

        async def _safe_provide(
            name: str, comp: Component
        ) -> Slice:
            start = time.monotonic()

            def _stamp(slice_obj: Slice, *, status: str) -> Slice:
                dur_ms = (time.monotonic() - start) * 1000.0
                try:
                    meta = dict(getattr(slice_obj, "meta", None) or {})
                    meta.setdefault("duration_ms", round(dur_ms, 1))
                    meta.setdefault("status", status)
                    slice_obj.meta = meta
                except Exception:  # noqa: BLE001 — meta 不可写不阻断
                    pass
                slow = (
                    per_component_timeout_s is not None
                    and dur_ms > per_component_timeout_s * 1000.0 * 0.6
                )
                if status != "ok" or slow:
                    logger.info(
                        "assembler.component_done",
                        component=name,
                        duration_ms=round(dur_ms, 1),
                        status=status,
                    )
                return slice_obj

            async def _run() -> Slice:
                result = await comp.provide(ctx)
                if not isinstance(result, Slice):
                    logger.warning(
                        "assembler.component_returned_non_slice",
                        component=name,
                        returned=type(result).__name__,
                    )
                    return Slice(component_name=name, meta={"error": "non_slice"})
                return result

            try:
                if per_component_timeout_s is None:
                    return _stamp(await _run(), status="ok")
                return _stamp(
                    await asyncio.wait_for(_run(), timeout=per_component_timeout_s),
                    status="ok",
                )
            except asyncio.TimeoutError:
                # 单组件超时 —— 只降级它，不影响别的组件（F1 核心修复）。
                logger.warning(
                    "assembler.component_timed_out",
                    component=name,
                    timeout_ms=(
                        per_component_timeout_s * 1000.0
                        if per_component_timeout_s is not None
                        else None
                    ),
                )
                return _stamp(
                    Slice(component_name=name, meta={"error": "timeout"}),
                    status="timeout",
                )
            except Exception as exc:  # defence-in-depth
                logger.warning(
                    "assembler.component_raised",
                    component=name,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return _stamp(
                    Slice(
                        component_name=name,
                        meta={"error": str(exc), "error_type": type(exc).__name__},
                    ),
                    status="error",
                )

        # 每个 _safe_provide 自带 per-component 超时 + 异常兜底，永不抛，
        # 慢组件不会再拖垮快组件。外层留一个宽松兜底（整体预算 + 0.5s
        # overhead）防极端调度饥饿 / 同步阻塞 loop。
        coros = [_safe_provide(name, comp) for name, comp in to_run]
        if timeout_ms is None:
            results = await asyncio.gather(*coros, return_exceptions=False)
        else:
            outer_timeout_s = timeout_ms / 1000.0 + 0.5
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*coros, return_exceptions=False),
                    timeout=outer_timeout_s,
                )
            except asyncio.TimeoutError:
                # per-component 已兜底，走到这里说明事件循环被同步阻塞
                # （某组件内有 blocking call）—— 仍降级而非崩。
                logger.warning(
                    "assembler.fanout_timed_out",
                    timeout_ms=timeout_ms,
                    outer_timeout_ms=outer_timeout_s * 1000.0,
                    pending=[n for n, _ in to_run],
                )
                results = [
                    Slice(component_name=n, meta={"error": "timeout"})
                    for n, _ in to_run
                ]

        # Preserve registration order for stable output.
        return list(results)
