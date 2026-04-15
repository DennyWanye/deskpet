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
from typing import AsyncIterator

import structlog

from observability.metrics import llm_ttft_seconds
from providers.base import LLMProvider
from router.types import BudgetHook, allow_all_budget

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
        budget_hook: BudgetHook | None = None,
    ) -> None:
        self._local = local
        self._cloud = cloud
        self._strategy = strategy
        # S6 ships the type + a no-op default. S8 will replace the default
        # with BillingLedger's real hook without changing the signature.
        self._budget_hook: BudgetHook = budget_hook or allow_all_budget
        self._local_state = _ProviderState()
        self._cloud_state = _ProviderState()

    async def _stream_with_ttft(
        self,
        provider: LLMProvider,
        provider_label: str,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        """Wrap a provider's chat_stream, observing TTFT on first yield.

        We deliberately capture ``t0`` inside this generator (not before
        the caller iterates) so the timestamp reflects the moment the
        iterator is actually advanced.
        """
        t0 = _now()
        first = True
        async for tok in provider.chat_stream(
            messages, temperature=temperature, max_tokens=max_tokens
        ):
            # Only a truthy (non-empty) chunk counts as the first token.
            # Some providers yield an empty string as a keep-alive before
            # the real content arrives; observing TTFT on that would
            # record a near-zero sample and skew the histogram.
            if first and tok:
                llm_ttft_seconds.labels(
                    provider=provider_label,
                    model=getattr(provider, "model", "unknown"),
                ).observe(_now() - t0)
                first = False
            yield tok

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        force_cloud: bool = False,
    ) -> AsyncIterator[str]:
        if self._strategy != RoutingStrategy.LOCAL_FIRST:
            raise NotImplementedError(
                f"strategy {self._strategy} not implemented in P2-1-S2"
            )

        if force_cloud:
            async for tok in self._stream_cloud(messages, temperature, max_tokens):
                yield tok
            return

        # local_first
        if self._local is not None:
            local_state = self._local_state
            if local_state.circuit_state_now() != _CircuitState.OPEN:
                if await self._check_health(self._local, local_state):
                    try:
                        async for tok in self._stream_with_ttft(
                            self._local,
                            "local",
                            messages,
                            temperature=temperature,
                            max_tokens=max_tokens,
                        ):
                            yield tok
                        local_state.record_chat_success()
                        return
                    except Exception as exc:
                        local_state.record_chat_failure()
                        logger.warning(
                            "router_local_chat_failed_falling_back_cloud",
                            error=str(exc),
                            consecutive_failures=local_state.consecutive_failures,
                        )

        # local skipped or local failed — try cloud
        async for tok in self._stream_cloud(messages, temperature, max_tokens):
            yield tok

    async def _stream_cloud(
        self, messages: list[dict[str, str]], temperature: float, max_tokens: int
    ) -> AsyncIterator[str]:
        if self._cloud is None:
            raise LLMUnavailableError(
                "cloud provider not configured and local unavailable"
            )
        cloud_state = self._cloud_state
        if cloud_state.circuit_state_now() == _CircuitState.OPEN:
            raise LLMUnavailableError(
                "cloud circuit breaker OPEN, retry in <30s"
            )
        if not await self._check_health(self._cloud, cloud_state):
            raise LLMUnavailableError(
                "cloud provider health_check failed and local unavailable"
            )
        try:
            async for tok in self._stream_with_ttft(
                self._cloud,
                "cloud",
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                yield tok
            cloud_state.record_chat_success()
        except Exception as exc:
            # Symmetry with local-failure path: don't poison the health cache
            # here. The circuit breaker is the source of truth for "this
            # provider is broken"; once it OPENs after 3 failures, the gate
            # at line 169 short-circuits before _check_health runs. Caching
            # False here would also keep the public health_check() returning
            # False for 30s after the cloud comes back, which masks recovery.
            cloud_state.record_chat_failure()
            logger.error("router_cloud_chat_failed", error=str(exc), provider="cloud")
            raise LLMUnavailableError(f"cloud chat failed: {exc}") from exc

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
