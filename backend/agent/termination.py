"""P6 — TerminationGate state machine.

Centralized termination decisions for AgentLoop. Hard limits (turns, tools,
wall-clock, budget) are non-negotiable and enforced by this module rather
than relying on the LLM honoring system messages.

See openspec/changes/p6-agent-loop-refactor/design.md §"模块 1: TerminationGate".

Design references:
- Claude Code SDK ResultMessage.subtype — enum termination reasons.
- Hermes Agent IterationBudget — per-conversation state machine.
- LangGraph create_react_agent — `_are_more_steps_needed` style
  (bool, reason) return tuple.
- LangGraph lesson — per-tool consecutive counter so one degraded tool
  cannot exhaust the global budget.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class TerminationReason(str, Enum):
    """All possible termination reasons.

    Inspired by Claude Code SDK's ResultMessage.subtype. Each value
    corresponds to a user-observable state.
    """

    # 自然终止 (good)
    SUCCESS = "success"
    USER_INTERRUPTED = "user_interrupted"

    # 硬限制 (we actively break, state observable)
    HARD_MAX_TURNS = "error_max_turns"
    HARD_TOOL_BUDGET = "error_tool_budget"
    HARD_WALL_CLOCK = "error_wall_clock_exceeded"
    HARD_MAX_BUDGET_USD = "error_max_budget_usd"

    # 错误状态
    PERMANENT_TOOL_ERROR = "permanent_tool_error"
    ALL_PROVIDERS_FAILED = "all_providers_failed"
    CONTEXT_BUDGET_BLOCK = "context_budget_block"
    HALLUCINATION_DETECTED = "hallucination"
    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"


@dataclass
class GateConfig:
    """All hard limits in one place.

    Can be overridden by [supervisor] / [agent] config sections.
    """

    max_turns: int = 50
    tool_budget_hard: int = 40
    wall_clock_seconds: float = 600.0  # NEW: 10min hard break
    max_budget_usd: float | None = None
    per_tool_max_consecutive: int = 5  # NEW: same tool 5x in a row → break


@dataclass
class GateState:
    """Internal state machine state. Hermes-inspired transition tracking."""

    started_at: float = 0.0
    turns_used: int = 0
    tools_used: int = 0
    cost_usd: float = 0.0
    # Per-tool consecutive counter (LangGraph lesson)
    per_tool_consecutive: dict[str, int] = field(default_factory=dict)
    # Why did the last iteration continue? (Hermes-inspired)
    last_transition: str = "init"
    # Set True on first terminate(); idempotent thereafter
    terminated: bool = False
    terminated_reason: TerminationReason | None = None


class TerminationGate:
    """Centralized termination decision-maker.

    AgentLoop polls `allows_call()` and `allows_tool(name)` at each critical
    point and treats `(False, reason)` as a hard stop with an observable
    reason.

    The `clock` constructor parameter exists for testability — production
    code uses `time.time`; tests inject a fake to advance the wall clock.
    """

    def __init__(
        self,
        config: GateConfig | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config or GateConfig()
        self._clock = clock
        self.state = GateState()
        # Initialise started_at via the injected clock so tests can pin t0.
        self.state.started_at = self._clock()

    # ------------------------------------------------------------------ #
    # Decision entry points (pure-style for testability)
    # ------------------------------------------------------------------ #

    def allows_call(self) -> tuple[bool, TerminationReason | None]:
        """Check before each LLM call."""
        if self.state.terminated:
            return (False, self.state.terminated_reason)
        if self.state.turns_used >= self.config.max_turns:
            return (False, TerminationReason.HARD_MAX_TURNS)
        elapsed = self._clock() - self.state.started_at
        if elapsed > self.config.wall_clock_seconds:
            return (False, TerminationReason.HARD_WALL_CLOCK)
        if (
            self.config.max_budget_usd is not None
            and self.state.cost_usd >= self.config.max_budget_usd
        ):
            return (False, TerminationReason.HARD_MAX_BUDGET_USD)
        return (True, None)

    def allows_tool(self, tool_name: str) -> tuple[bool, TerminationReason | None]:
        """Check before each tool dispatch."""
        if self.state.terminated:
            return (False, self.state.terminated_reason)
        if self.state.tools_used >= self.config.tool_budget_hard:
            return (False, TerminationReason.HARD_TOOL_BUDGET)
        consec = self.state.per_tool_consecutive.get(tool_name, 0)
        if consec >= self.config.per_tool_max_consecutive:
            return (False, TerminationReason.HALLUCINATION_DETECTED)
        return (True, None)

    # ------------------------------------------------------------------ #
    # State advancement
    # ------------------------------------------------------------------ #

    def record_turn(self, cost_delta_usd: float = 0.0) -> None:
        self.state.turns_used += 1
        self.state.cost_usd += cost_delta_usd

    def record_tool_call(self, tool_name: str) -> None:
        self.state.tools_used += 1
        self.state.per_tool_consecutive[tool_name] = (
            self.state.per_tool_consecutive.get(tool_name, 0) + 1
        )
        # Any other tool call resets that other tool's consecutive counter.
        for other in list(self.state.per_tool_consecutive.keys()):
            if other != tool_name:
                self.state.per_tool_consecutive[other] = 0

    def record_final_answer(self) -> None:
        """Called when LLM emits stop_reason=end_turn."""
        self.terminate(TerminationReason.SUCCESS)

    def record_error(self, reason: TerminationReason) -> None:
        """Called on various ErrorEvent paths."""
        self.terminate(reason)

    def terminate(self, reason: TerminationReason) -> None:
        """Idempotent: first call wins, subsequent calls are no-ops."""
        if not self.state.terminated:
            self.state.terminated = True
            self.state.terminated_reason = reason

    # ------------------------------------------------------------------ #
    # Reporting
    # ------------------------------------------------------------------ #

    def summary(self) -> dict[str, Any]:
        """For ResultMessage / WS events."""
        return {
            "reason": (
                self.state.terminated_reason.value
                if self.state.terminated_reason is not None
                else "running"
            ),
            "turns_used": self.state.turns_used,
            "tools_used": self.state.tools_used,
            "elapsed_seconds": self._clock() - self.state.started_at,
            "cost_usd": self.state.cost_usd,
        }


__all__ = [
    "TerminationReason",
    "GateConfig",
    "GateState",
    "TerminationGate",
]
