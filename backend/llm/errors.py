"""LLM provider error hierarchy.

Error classification drives agent loop behavior:
    - LLMRateLimitError      → backoff + retry same provider (up to 3x)
    - LLMTimeoutError        → fallback chain (next provider)
    - LLMAuthError           → drop provider from registry, fallback
    - LLMBudgetExceededError → terminate turn, surface user-facing message
    - LLMProviderError       → generic terminal failure
"""
from __future__ import annotations

from typing import Optional


class LLMProviderError(Exception):
    """Terminal failure from an LLM provider. Does not retry same provider."""

    def __init__(
        self,
        message: str,
        *,
        provider: Optional[str] = None,
        status_code: Optional[int] = None,
        retriable: bool = False,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.retriable = retriable


class LLMRateLimitError(LLMProviderError):
    """HTTP 429. retry_after in seconds if provider sent Retry-After header."""

    def __init__(
        self,
        message: str,
        *,
        provider: Optional[str] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message, provider=provider, status_code=429, retriable=True)
        self.retry_after = retry_after


class LLMTimeoutError(LLMProviderError):
    """Network / request timeout. Retriable on a *different* provider."""

    def __init__(self, message: str, *, provider: Optional[str] = None) -> None:
        super().__init__(message, provider=provider, retriable=True)


class LLMAuthError(LLMProviderError):
    """401 / 403. Key is invalid or expired — drop provider from registry."""

    def __init__(self, message: str, *, provider: Optional[str] = None) -> None:
        super().__init__(message, provider=provider, status_code=401, retriable=False)


class LLMBudgetExceededError(LLMProviderError):
    """Daily USD cap reached. No provider will take this request today."""

    def __init__(self, message: str = "daily budget exceeded") -> None:
        super().__init__(message, retriable=False)
