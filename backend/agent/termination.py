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

import hashlib
import json
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

    # P6 bugfix 2026-05-14 (用户反馈最终版): "auto-mode 下长任务只要没死循环
    # 就该一直跑下去"。time/count cap 改成"实质 disabled"（极大值），真死循环
    # 检测交给 per_tool_max_consecutive（args-aware）单独兜底。
    #
    # 历史调参链：
    #   max_turns: 50 → 200 (R1) → 10000 (R8 用户反馈)
    #   tool_budget_hard: 40 → 200 (R1) → 10000 (R8 用户反馈)
    #   wall_clock_seconds: 600 → 1800 (R7) → None (R8 用户反馈 disabled)
    #
    # 现在的真死循环防御靠：
    #   1. per_tool_max_consecutive=8 (args-aware)：同工具+同参数 8 次必死循环
    #   2. ContextManager B3 token budget：上下文过大也是天然中止信号
    #   3. supervisor watchdog：观察 status='running' 但无事件可见的真卡死
    # 这三层比"按时间/次数硬切"更精准，不会误杀正常长任务。
    max_turns: int = 10000
    tool_budget_hard: int = 10000
    # None 表示禁用 wall-clock 硬上限（按用户期望"一直跑下去"）
    wall_clock_seconds: float | None = None
    max_budget_usd: float | None = None
    # P6 bugfix 2026-05-13 (live-test): bumped 5 → 8. Five was too tight —
    # legitimate "list_directory + glob + read 5 different files" sequences
    # frequently hit it. The counter is now ALSO args-aware (see
    # record_tool_call), so 5 reads of FIVE DIFFERENT files no longer count
    # as consecutive — only 8 reads of the SAME path triggers the cap.
    per_tool_max_consecutive: int = 8


@dataclass
class GateState:
    """Internal state machine state. Hermes-inspired transition tracking."""

    started_at: float = 0.0
    turns_used: int = 0
    tools_used: int = 0
    cost_usd: float = 0.0
    # Per-tool consecutive counter (LangGraph lesson). The COUNT is keyed
    # by tool name; _per_tool_last_sig holds the args-signature of the most
    # recent call so we can distinguish "read_file × 5 same path" (real
    # death loop) from "read_file × 5 different paths" (legitimate
    # exploration).
    per_tool_consecutive: dict[str, int] = field(default_factory=dict)
    _per_tool_last_sig: dict[str, str] = field(default_factory=dict, repr=False)
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
        # P6 bugfix 2026-05-14: wall_clock_seconds=None → disabled。
        # 用户期望 "auto-mode 下长任务只要没卡死循环就一直跑下去"，
        # 真死循环检测交给 per_tool_max_consecutive。
        if self.config.wall_clock_seconds is not None:
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

    def record_tool_call(self, tool_name: str, *, args: Any = None) -> None:
        """Record a tool dispatch.

        ``args`` is optional but recommended — when supplied, the per-tool
        consecutive counter only increments if the args match the previous
        call to the same tool. Different args reset the counter to 1.

        Rationale: pre-fix (P6 v1) the counter was name-only, which caused
        false "hallucination" detections on legitimate sequential reads of
        different files (e.g. ``read_file A.py``, ``read_file B.py``, ...).
        Args-aware matching distinguishes a death loop (same args repeated)
        from systematic exploration (different args each call).
        """
        self.state.tools_used += 1
        sig = self._args_signature(args)
        last_sig = self.state._per_tool_last_sig.get(tool_name)
        if last_sig is not None and last_sig == sig:
            # Same tool + same args → count up
            self.state.per_tool_consecutive[tool_name] = (
                self.state.per_tool_consecutive.get(tool_name, 0) + 1
            )
        else:
            # New call (different args, or first call to this tool) → reset to 1.
            self.state.per_tool_consecutive[tool_name] = 1
            self.state._per_tool_last_sig[tool_name] = sig
        # Any other tool call resets that other tool's consecutive counter.
        for other in list(self.state.per_tool_consecutive.keys()):
            if other != tool_name:
                self.state.per_tool_consecutive[other] = 0
                self.state._per_tool_last_sig.pop(other, None)

    @staticmethod
    def _args_signature(args: Any) -> str:
        """Stable 16-char hash of args for consecutive-call detection.

        ``None`` and unhashable args fall back to a string repr — both still
        produce stable signatures that won't accidentally collide with real
        json-serializable args.
        """
        if args is None:
            return "<none>"
        try:
            payload = json.dumps(args, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            payload = repr(args)
        return hashlib.md5(payload.encode("utf-8", errors="replace")).hexdigest()[:16]

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
