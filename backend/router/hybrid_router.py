"""HybridRouter — local_first LLM 路由 + circuit breaker + 预算钩子.

Implements LLMProvider Protocol, drop-in replacement for a single
OpenAICompatibleProvider in service_context.llm_engine.

Design references:
  docs/superpowers/specs/2026-04-15-p2-1-design.md §4.4 / §3.3
  docs/superpowers/plans/2026-04-15-p2-1-s2-hybrid-router.md
"""
from __future__ import annotations

import enum
from typing import AsyncIterator, Callable

import structlog

from providers.base import LLMProvider

logger = structlog.get_logger()


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

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> AsyncIterator[str]:
        raise NotImplementedError("filled in Task 5")
        yield  # pragma: no cover  (make it an async generator)

    async def health_check(self) -> bool:
        raise NotImplementedError("filled in Task 4")
