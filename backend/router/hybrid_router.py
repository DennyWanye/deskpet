"""HybridRouter — local_first LLM 路由 + circuit breaker + 预算钩子.

Implements LLMProvider Protocol, drop-in replacement for a single
OpenAICompatibleProvider in service_context.llm_engine.

Design references:
  docs/superpowers/specs/2026-04-15-p2-1-design.md §4.4 / §3.3
  docs/superpowers/plans/2026-04-15-p2-1-s2-hybrid-router.md
"""
from __future__ import annotations

import enum
import time
from typing import AsyncIterator, Callable

import structlog

from providers.base import LLMProvider

logger = structlog.get_logger()

_HEALTH_TTL_SECONDS = 30.0
_CIRCUIT_OPEN_AFTER_FAILURES = 3
_CIRCUIT_OPEN_DURATION_SECONDS = 30.0


def _now() -> float:
    """Monkeypatchable time source for deterministic tests."""
    return time.monotonic()


class _CircuitState(str, enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class _ProviderState:
    """Per-provider rolling state: circuit breaker + health cache.

    Held by HybridRouter as a private attribute; not part of public API.
    """

    def __init__(self) -> None:
        self.consecutive_failures: int = 0
        self.circuit: _CircuitState = _CircuitState.CLOSED
        self._opened_at: float | None = None
        self._health_value: bool | None = None
        self._health_at: float | None = None

    # --- circuit breaker ---

    def record_chat_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= _CIRCUIT_OPEN_AFTER_FAILURES:
            self.circuit = _CircuitState.OPEN
            self._opened_at = _now()

    def record_chat_success(self) -> None:
        self.consecutive_failures = 0
        self.circuit = _CircuitState.CLOSED
        self._opened_at = None

    def circuit_state_now(self) -> _CircuitState:
        """Returns logical state, transitioning OPEN→HALF_OPEN if cooldown elapsed."""
        if self.circuit == _CircuitState.OPEN and self._opened_at is not None:
            if _now() - self._opened_at >= _CIRCUIT_OPEN_DURATION_SECONDS:
                return _CircuitState.HALF_OPEN
        return self.circuit

    # --- health cache ---

    def cache_health(self, value: bool) -> None:
        self._health_value = value
        self._health_at = _now()

    def cached_health(self) -> bool | None:
        if self._health_at is None:
            return None
        if _now() - self._health_at > _HEALTH_TTL_SECONDS:
            return None
        return self._health_value


class LLMUnavailableError(RuntimeError):
    """All routes exhausted (local + cloud both failed or unconfigured)."""


class RoutingStrategy(str, enum.Enum):
    LOCAL_FIRST = "local_first"
    CLOUD_FIRST = "cloud_first"      # P2-1-S2 unimplemented
    COST_AWARE = "cost_aware"        # P2-1-S2 unimplemented
    LATENCY_AWARE = "latency_aware"  # P2-1-S2 unimplemented


class HybridRouter:
    """Routes chat_stream calls between a local and a cloud provider.

    S2 implements only `local_first`. Other strategies parse from config
    but raise NotImplementedError on use, so a future slice can fill them
    in without changing the public surface.
    """

    def __init__(
        self,
        *,
        local: LLMProvider | None,
        cloud: LLMProvider | None,
        strategy: RoutingStrategy = RoutingStrategy.LOCAL_FIRST,
        budget_check: Callable[[], bool] | None = None,
    ) -> None:
        self._local = local
        self._cloud = cloud
        self._strategy = strategy
        self._budget_check = budget_check
        self._local_state = _ProviderState()
        self._cloud_state = _ProviderState()

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> AsyncIterator[str]:
        raise NotImplementedError("filled in Task 5")
        yield  # pragma: no cover  (make it an async generator)

    async def _check_health(self, provider: LLMProvider, state: _ProviderState) -> bool:
        cached = state.cached_health()
        if cached is not None:
            return cached
        try:
            ok = await provider.health_check()
        except Exception as exc:
            logger.warning("router_health_check_raised", error=str(exc))
            ok = False
        state.cache_health(ok)
        return ok

    async def health_check(self) -> bool:
        if self._local is not None and await self._check_health(self._local, self._local_state):
            return True
        if self._cloud is not None and await self._check_health(self._cloud, self._cloud_state):
            return True
        return False
