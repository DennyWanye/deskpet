"""Base :class:`Component` protocol + :class:`ComponentContext` (P4-S7).

Every component receives the same ``ComponentContext`` and returns a
:class:`Slice`. The context holds everything a component might need —
user message, history, policy, memory manager, tool registry, and a
``deadline_ms`` used by slow components (embedding search, etc.) to
self-trim when the overall budget is tight.

Components are async and MUST NOT raise on normal operation. On failure
they MUST return an empty slice with ``meta={"error": str(exc)}``. The
registry fans them out via ``asyncio.gather(return_exceptions=True)``
as a safety net, but silent degradation keeps the bundle whole.

Spec: Requirement "Component Registry and Parallel Fan-out".
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from deskpet.agent.assembler.bundle import AssemblyPolicy, Slice


@dataclass
class ComponentContext:
    """Runtime context passed to every component on every turn.

    The assembler builds this once per ``assemble()`` call and passes
    the same instance to all components. Components MUST NOT mutate it.
    """

    task_type: str
    policy: AssemblyPolicy
    user_message: str
    history: list[dict[str, Any]] = field(default_factory=list)
    session_id: Optional[str] = None
    # Injected dependencies — may be None when unit tests swap in fakes.
    memory_manager: Any = None
    tool_registry: Any = None
    skill_registry: Any = None  # P4-S10 wire-in; None-safe for S7.
    mcp_manager: Any = None  # P4-S9 wire-in; None-safe for S7.
    config: dict[str, Any] = field(default_factory=dict)
    # Wall-clock deadline for slow components; assembler enforces timeout
    # at the registry level but components can pre-cut work if very
    # tight. ``deadline_wall_time`` is ``time.monotonic()`` units.
    deadline_wall_time: Optional[float] = None

    def time_remaining_ms(self) -> Optional[float]:
        """How long until ``deadline_wall_time``. None if no deadline set."""
        if self.deadline_wall_time is None:
            return None
        remaining = self.deadline_wall_time - time.monotonic()
        return max(0.0, remaining * 1000.0)


@runtime_checkable
class Component(Protocol):
    """Duck-typed component contract."""

    name: str

    async def provide(self, ctx: ComponentContext) -> Slice:
        """Return this component's contribution to the bundle.

        MUST NOT raise. On failure return ``Slice(component_name=self.name,
        meta={"error": str(exc)})``.
        """
        ...
