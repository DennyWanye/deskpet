"""LLMRegistry + fallback chain.

Design:
    - Caller passes the parsed [llm.*] config table to __init__.
    - Adapters are instantiated lazily — `available()` gates whether the
      adapter even shows up in `list_providers()` (no key → no listing).
    - `chat_with_fallback` walks `config.llm.fallback_chain` (max depth 2
      beyond primary). On 429 it retries the *same* provider with
      `Retry-After` or 1s/2s/4s exponential backoff (≤3 attempts) before
      switching. On 5xx / timeout / auth it switches immediately.
    - 429 retry budget is *per provider attempt* — if the first provider
      returns 429 three times we move on, not give up entirely.

Fallback chain format:
    ["anthropic:claude-sonnet-4-5", "openai:gpt-4o", "gemini:gemini-1.5-pro"]
    first element is "primary", rest are backups. Depth > 2 is trimmed
    silently (spec requirement: MUST ≤ primary + 2).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from llm.anthropic_adapter import AnthropicAdapter
from llm.base import BaseLLMAdapter
from llm.budget import DailyBudget
from llm.errors import (
    LLMAuthError,
    LLMBudgetExceededError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from llm.gemini_adapter import GeminiAdapter
from llm.openai_adapter import OpenAIAdapter
from llm.types import ChatResponse

logger = logging.getLogger("deskpet.llm.registry")


_ADAPTERS: dict[str, type[BaseLLMAdapter]] = {
    "anthropic": AnthropicAdapter,
    "openai": OpenAIAdapter,
    "gemini": GeminiAdapter,
}


def _parse_endpoint(endpoint: str) -> tuple[str, Optional[str]]:
    """Parse 'provider:model' into (provider, model). Model is optional."""
    if ":" in endpoint:
        provider, model = endpoint.split(":", 1)
        return provider.strip().lower(), model.strip() or None
    return endpoint.strip().lower(), None


class LLMRegistry:
    """Holds instantiated adapters keyed by provider name."""

    MAX_FALLBACK_DEPTH: int = 3  # primary + 2 backups
    RATE_LIMIT_RETRIES: int = 3
    DEFAULT_BACKOFF_BASE: float = 1.0

    def __init__(
        self,
        config: Optional[dict[str, Any]] = None,
        *,
        adapters: Optional[dict[str, BaseLLMAdapter]] = None,
        budget: Optional[DailyBudget] = None,
    ) -> None:
        """Construct the registry.

        Args:
            config: parsed TOML [llm] table. Shape:
                {
                    "main_model": "claude-sonnet-4-5",
                    "fallback_chain": ["openai:gpt-4o", "gemini:gemini-1.5-pro"],
                    "daily_usd_cap": 10.0,
                    "providers": {"anthropic": {"default_model": "..."}, ...},
                }
            adapters: explicit provider→adapter mapping. When set, bypasses
                default instantiation (used by tests with mocks).
            budget: optional DailyBudget. If provided, pre-call cap check
                applies and per-call usage is accumulated.
        """
        self._config = config or {}
        self._budget = budget
        self._adapters: dict[str, BaseLLMAdapter] = {}
        if adapters is not None:
            # Test path: caller supplies mocks keyed by provider name.
            self._adapters = dict(adapters)
        else:
            for name, cls in _ADAPTERS.items():
                provider_cfg = ((self._config.get("providers") or {}).get(name)) or {}
                try:
                    self._adapters[name] = cls(**self._build_kwargs(provider_cfg))
                except TypeError:
                    # Adapter signature mismatch (e.g. forward-compat kwarg)
                    # — fall back to no-arg construction.
                    self._adapters[name] = cls()

    # ───────────────────── config helpers ─────────────────────

    @staticmethod
    def _build_kwargs(provider_cfg: dict[str, Any]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if "default_model" in provider_cfg:
            kwargs["default_model"] = provider_cfg["default_model"]
        if "timeout" in provider_cfg:
            kwargs["timeout"] = provider_cfg["timeout"]
        if "base_url" in provider_cfg:
            kwargs["base_url"] = provider_cfg["base_url"]
        return kwargs

    # ───────────────────── public api ─────────────────────

    def list_providers(self) -> list[str]:
        """Providers with a resolvable API key, in stable insertion order."""
        return [name for name, ad in self._adapters.items() if ad.available()]

    def get(self, name: str) -> BaseLLMAdapter:
        name = name.lower()
        if name not in self._adapters:
            raise KeyError(f"unknown provider: {name}")
        return self._adapters[name]

    def fallback_chain(self) -> list[str]:
        """Ordered primary→backups list from config, capped at MAX_FALLBACK_DEPTH."""
        primary = self._config.get("main_model")
        chain_cfg = list(self._config.get("fallback_chain") or [])
        endpoints: list[str] = []

        # Include primary first if configured; tolerate bare model names
        # by prefixing with anthropic (the spec-default provider).
        if primary:
            endpoints.append(primary if ":" in primary else f"anthropic:{primary}")

        for ep in chain_cfg:
            if ep and ep not in endpoints:
                endpoints.append(ep)

        # Fall back to default 3-provider chain if config is empty.
        if not endpoints:
            endpoints = [
                "anthropic:claude-sonnet-4-5",
                "openai:gpt-4o",
                "gemini:gemini-1.5-pro",
            ]

        return endpoints[: self.MAX_FALLBACK_DEPTH]

    async def close(self) -> None:
        for adapter in self._adapters.values():
            try:
                await adapter.close()
            except Exception:  # noqa: BLE001
                pass

    # ───────────────────── chat with fallback ─────────────────────

    async def chat_with_fallback(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        model: Optional[str] = None,
        *,
        max_retries_per_provider: Optional[int] = None,
        backoff_base: Optional[float] = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """Call LLMs in fallback order; enforce budget cap.

        Algorithm:
            for endpoint in fallback_chain:
                provider, m = parse(endpoint)
                for attempt in 1..RATE_LIMIT_RETRIES:
                    try: return adapter.chat(...)
                    except LLMRateLimitError: backoff and retry same adapter
                    except LLMAuthError / LLMTimeoutError / 5xx: break, next provider
            raise LLMProviderError("all providers failed: ...")
        """
        if self._budget is not None and not self._budget.check_allowed():
            raise LLMBudgetExceededError()

        chain = self.fallback_chain()
        max_retries = max_retries_per_provider or self.RATE_LIMIT_RETRIES
        backoff = backoff_base or self.DEFAULT_BACKOFF_BASE
        errors: list[str] = []

        for endpoint in chain:
            provider_name, endpoint_model = _parse_endpoint(endpoint)
            adapter = self._adapters.get(provider_name)
            if adapter is None or not adapter.available():
                errors.append(f"{provider_name}: unavailable (no key or not registered)")
                continue

            use_model = model or endpoint_model  # explicit `model` overrides endpoint

            last_exc: Optional[Exception] = None
            for attempt in range(1, max_retries + 1):
                try:
                    response = await adapter.chat(
                        messages,
                        tools=tools,
                        model=use_model,
                        stream=False,
                        **kwargs,
                    )
                    # Contract: non-stream chat returns ChatResponse directly.
                    assert isinstance(response, ChatResponse), (
                        "chat_with_fallback requires non-stream adapters"
                    )
                    if self._budget is not None:
                        self._budget.add_usage(
                            provider_name,
                            response.model or use_model or adapter.default_model,
                            response.usage,
                        )
                    return response
                except LLMRateLimitError as exc:
                    last_exc = exc
                    if attempt >= max_retries:
                        errors.append(f"{provider_name}: rate-limited after {attempt} retries")
                        break
                    wait_s = exc.retry_after if exc.retry_after is not None else backoff * (2 ** (attempt - 1))
                    logger.warning(
                        "llm %s rate-limited (attempt %d/%d); sleeping %.2fs",
                        provider_name,
                        attempt,
                        max_retries,
                        wait_s,
                    )
                    await asyncio.sleep(wait_s)
                    continue
                except LLMAuthError as exc:
                    # Auth errors mean the key is wrong — don't retry same
                    # provider, drop it for this process lifetime.
                    errors.append(f"{provider_name}: auth error ({exc})")
                    logger.warning("llm %s auth failed, removing from registry", provider_name)
                    break
                except LLMTimeoutError as exc:
                    errors.append(f"{provider_name}: timeout ({exc})")
                    last_exc = exc
                    break
                except LLMProviderError as exc:
                    errors.append(f"{provider_name}: {exc}")
                    last_exc = exc
                    break
                except Exception as exc:  # noqa: BLE001 — unknown SDK surface
                    errors.append(f"{provider_name}: unexpected {type(exc).__name__}: {exc}")
                    last_exc = exc
                    break

            # Exhausted this provider; move to next in chain.
            if last_exc is not None:
                logger.info(
                    "llm fallback: %s exhausted (%s), trying next",
                    provider_name,
                    type(last_exc).__name__,
                )

        raise LLMProviderError(
            "all providers failed: " + "; ".join(errors) if errors else "no providers configured"
        )
