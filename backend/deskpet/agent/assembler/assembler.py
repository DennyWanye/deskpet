"""ContextAssembler — assembles one :class:`ContextBundle` per user turn.

Public surface (spec Requirement "Pre-loop Context Assembly"):

    assembler = ContextAssembler(
        component_registry=...,
        policies=...,
        classifier=...,
        budget_allocator=...,
    )
    bundle = await assembler.assemble(
        user_message="...",
        history=[...],
        memory_manager=mm,
        tool_registry=tr,
        session_id="...",
    )
    assembler.feedback(bundle, used_tools=[...], final_response="...")

Degradation (task 12.13):
- ``config.context.assembler.enabled = False`` → ``assemble()`` returns
  a minimal bundle that exposes every tool from the registry (legacy
  Hermes path) with only the persona block filled in. The caller still
  gets something usable.

Decisions telemetry (task 12.11):
- Every assemble() call writes an :class:`AssemblyDecisions` object into
  the bundle and appends the last N to the in-memory ring buffer
  returned by :meth:`recent_decisions` (for the Context Trace UI).
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, Optional

import structlog

from deskpet.agent.assembler.bundle import (
    AssemblyDecisions,
    AssemblyPolicy,
    ComponentTrace,
    ContextBundle,
    Slice,
    TASK_TYPES,
)
from deskpet.agent.assembler.budget import BudgetAllocator, BudgetResult
from deskpet.agent.assembler.classifier import ClassifierResult, TaskClassifier
from deskpet.agent.assembler.components.base import ComponentContext
from deskpet.agent.assembler.registry import ComponentRegistry

logger = structlog.get_logger(__name__)


_MAX_DECISIONS_RING = 50


class ContextAssembler:
    """Assembles the per-turn :class:`ContextBundle`.

    Parameters
    ----------
    component_registry:
        Ordered :class:`ComponentRegistry` with the 6 built-in components
        (or subset) already registered.
    policies:
        ``{task_type: AssemblyPolicy}`` from :func:`load_policies`.
    classifier:
        :class:`TaskClassifier` with embedder + (optional) LLM registry.
    budget_allocator:
        :class:`BudgetAllocator`. Uses context_window * budget_ratio.
    enabled:
        ``False`` → assemble() returns legacy-mode bundle (see module doc).
    component_timeout_ms:
        Wall-clock cap for the parallel fan-out. Default 1500ms.
    """

    def __init__(
        self,
        *,
        component_registry: ComponentRegistry,
        policies: dict[str, AssemblyPolicy],
        classifier: TaskClassifier,
        budget_allocator: Optional[BudgetAllocator] = None,
        enabled: bool = True,
        component_timeout_ms: float = 1500.0,
    ) -> None:
        self._registry = component_registry
        self._policies = policies
        self._classifier = classifier
        self._budget = budget_allocator or BudgetAllocator()
        self._enabled = enabled
        self._component_timeout_ms = component_timeout_ms
        self._decisions_ring: deque[AssemblyDecisions] = deque(
            maxlen=_MAX_DECISIONS_RING
        )

    # ------------------------------------------------------------------
    # Config toggle
    # ------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        self._enabled = bool(value)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def assemble(
        self,
        user_message: str,
        *,
        history: Optional[list[dict[str, Any]]] = None,
        memory_manager: Any = None,
        tool_registry: Any = None,
        skill_registry: Any = None,
        mcp_manager: Any = None,
        config: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
        task_type_override: Optional[str] = None,
    ) -> ContextBundle:
        """Produce a ContextBundle. MUST NOT raise."""
        start = time.monotonic()

        # 1. Legacy bypass path (task 12.13).
        if not self._enabled:
            return self._legacy_bundle(
                user_message,
                tool_registry=tool_registry,
                config=config or {},
            )

        # 2. Classify.
        if task_type_override:
            task_type = (
                task_type_override
                if task_type_override in TASK_TYPES
                else "chat"
            )
            classifier_result = ClassifierResult(
                task_type=task_type,
                path="override",
                confidence=1.0,
                latency_ms=0.0,
                rationale="caller override",
            )
        else:
            try:
                classifier_result = await self._classifier.classify(
                    user_message
                )
            except Exception as exc:  # defensive — classifier should not raise
                logger.warning(
                    "assembler.classifier_crashed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                classifier_result = ClassifierResult(
                    task_type="chat",
                    path="default",
                    confidence=0.0,
                    latency_ms=0.0,
                    rationale=f"classifier crashed: {exc}",
                )

            # Unknown → chat (spec "Unknown task_type falls back to chat").
            if classifier_result.task_type not in TASK_TYPES:
                classifier_result = ClassifierResult(
                    task_type="chat",
                    path=classifier_result.path,
                    confidence=classifier_result.confidence,
                    latency_ms=classifier_result.latency_ms,
                    rationale=(
                        classifier_result.rationale
                        + " (unknown → chat fallback)"
                    ),
                )

        policy = self._policies.get(
            classifier_result.task_type, self._policies.get("chat")
        )
        if policy is None:
            # Both missing → last-resort chat policy.
            policy = AssemblyPolicy(task_type="chat")

        # 3. Fan out components in parallel.
        ctx = ComponentContext(
            task_type=policy.task_type,
            policy=policy,
            user_message=user_message,
            history=list(history or []),
            session_id=session_id,
            memory_manager=memory_manager,
            tool_registry=tool_registry,
            skill_registry=skill_registry,
            mcp_manager=mcp_manager,
            config=config or {},
            deadline_wall_time=time.monotonic()
            + self._component_timeout_ms / 1000.0,
        )

        slices = await self._registry.fanout(
            ctx, timeout_ms=self._component_timeout_ms
        )

        # 4. Allocate budget.
        budget_result = self._budget.allocate(
            slices, override_ratio=policy.budget_ratio
        )

        # 5. Stitch buckets into the bundle.
        bundle = self._stitch(classifier_result, budget_result, policy)

        # 6. Decisions trace.
        total_ms = (time.monotonic() - start) * 1000.0
        bundle.decisions = self._build_decisions(
            classifier_result,
            budget_result,
            assembly_latency_ms=total_ms,
            planned_tools=[
                _schema_name(s) for s in bundle.tool_schemas if _schema_name(s)
            ],
        )
        self._decisions_ring.append(bundle.decisions)
        return bundle

    def feedback(
        self,
        bundle: ContextBundle,
        *,
        used_tools: Optional[list[str]] = None,
        final_response: Optional[str] = None,
    ) -> None:
        """Record post-turn outcome for future adaptive learning (task 12.12).

        V1 stores used_tools + final_response length on the decisions
        record; no online learning yet.
        """
        if bundle is None or bundle.decisions is None:
            return
        if used_tools is not None:
            bundle.decisions.used_tools = list(used_tools)
        if final_response is not None:
            bundle.decisions.final_response_len = len(final_response)

    def recent_decisions(self, n: int = 20) -> list[dict[str, Any]]:
        """Return last N decisions as plain dicts (IPC-friendly)."""
        if n <= 0:
            return []
        if n >= len(self._decisions_ring):
            return [d.to_dict() for d in self._decisions_ring]
        return [d.to_dict() for d in list(self._decisions_ring)[-n:]]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _stitch(
        self,
        classifier_result: ClassifierResult,
        budget_result: BudgetResult,
        policy: AssemblyPolicy,
    ) -> ContextBundle:
        """Merge slices into the bundle's named buckets."""
        bundle = ContextBundle(task_type=classifier_result.task_type)

        frozen_chunks: list[str] = []
        dynamic_chunks: list[str] = []
        skill_chunks: list[str] = []
        seen_tool_names: set[str] = set()
        merged_schemas: list[dict[str, Any]] = []
        cost_hint: dict[str, int] = {}

        for sl in budget_result.slices:
            cost_hint[sl.component_name] = max(0, sl.tokens)
            if sl.tool_schemas:
                for schema in sl.tool_schemas:
                    name = _schema_name(schema)
                    if name in seen_tool_names:
                        continue
                    seen_tool_names.add(name)
                    merged_schemas.append(schema)
            if not sl.text_content:
                continue

            bucket = (sl.bucket or "frozen").lower()
            if bucket == "frozen":
                frozen_chunks.append(sl.text_content)
            elif bucket == "dynamic":
                dynamic_chunks.append(sl.text_content)
            elif bucket == "skill":
                skill_chunks.append(sl.text_content)
            else:
                # Unknown bucket → treat as frozen (safest default).
                frozen_chunks.append(sl.text_content)

        bundle.frozen_system = "\n\n".join(frozen_chunks).strip()
        bundle.memory_block = "\n\n".join(dynamic_chunks).strip()
        bundle.skill_prelude = "\n\n".join(skill_chunks).strip()
        bundle.tool_schemas = merged_schemas
        bundle.cost_hint = cost_hint
        return bundle

    def _build_decisions(
        self,
        classifier_result: ClassifierResult,
        budget_result: BudgetResult,
        *,
        assembly_latency_ms: float,
        planned_tools: list[str],
    ) -> AssemblyDecisions:
        components: dict[str, ComponentTrace] = {}
        for sl in budget_result.slices:
            meta = dict(sl.meta)
            latency = float(meta.pop("latency_ms", 0.0))
            included = meta.get("trimmed") != "dropped"
            components[sl.component_name] = ComponentTrace(
                tokens=max(0, sl.tokens),
                latency_ms=latency,
                included=included,
                meta=meta,
            )
        return AssemblyDecisions(
            task_type=classifier_result.task_type,
            classifier_path=classifier_result.path,
            classifier_latency_ms=classifier_result.latency_ms,
            classifier_confidence=classifier_result.confidence,
            assembly_latency_ms=assembly_latency_ms,
            components=components,
            budget_cut=list(budget_result.cut),
            total_tokens=budget_result.total_tokens,
            planned_tools=planned_tools,
        )

    def _legacy_bundle(
        self,
        user_message: str,
        *,
        tool_registry: Any,
        config: dict[str, Any],
    ) -> ContextBundle:
        """Bypass mode: exposes every tool, minimal memory, no classifier.

        Used when ``config.context.assembler.enabled = False`` — a
        hard-disable lever for emergency rollback.
        """
        schemas: list[dict[str, Any]] = []
        if tool_registry is not None:
            try:
                schemas = list(tool_registry.schemas() or [])
            except Exception as exc:
                logger.warning(
                    "assembler.legacy_mode_registry_error",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
        persona = (
            config.get("agent", {}).get("persona")
            if isinstance(config, dict)
            else None
        )
        bundle = ContextBundle(
            task_type="chat",
            frozen_system=str(persona or "").strip() or "DeskPet legacy mode.",
            tool_schemas=schemas,
        )
        bundle.decisions = AssemblyDecisions(
            task_type="chat",
            classifier_path="disabled",
            assembly_latency_ms=0.0,
            planned_tools=[
                _schema_name(s) for s in schemas if _schema_name(s)
            ],
        )
        self._decisions_ring.append(bundle.decisions)
        return bundle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _schema_name(schema: dict[str, Any]) -> str:
    if not isinstance(schema, dict):
        return ""
    if "function" in schema and isinstance(schema["function"], dict):
        return str(schema["function"].get("name", ""))
    return str(schema.get("name", ""))
