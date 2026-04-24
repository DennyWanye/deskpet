"""Base LLM adapter protocol.

Every provider adapter subclasses BaseLLMAdapter and implements:
    - async def chat(messages, tools=None, model=None, *, stream=False, **kwargs)
    - def available(self) -> bool    (api key present?)
    - async def close(self) -> None  (release client pools / sockets)

chat() contract:
    - non-stream: returns ChatResponse
    - stream=True: returns AsyncIterator[ChatChunk]

    messages: list of {"role": "system"|"user"|"assistant"|"tool", "content": ...}
    tools:    OpenAI function-calling format (even for Anthropic/Gemini —
              adapters convert internally). Aligns with ToolRegistry.schemas()
              output from P4-S5.
    model:    specific model name. None → adapter's default.
"""
from __future__ import annotations

import abc
from typing import Any, AsyncIterator, Optional, Union

from llm.types import ChatChunk, ChatResponse


class BaseLLMAdapter(abc.ABC):
    """Shared interface for all cloud LLM adapters."""

    #: provider short name ("anthropic" / "openai" / "gemini"). Subclasses set.
    name: str = ""

    #: default model if caller doesn't pass one. Subclasses set.
    default_model: str = ""

    @abc.abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        model: Optional[str] = None,
        *,
        stream: bool = False,
        **kwargs: Any,
    ) -> Union[ChatResponse, AsyncIterator[ChatChunk]]:
        """Run one chat completion.

        Raises:
            LLMAuthError: 401/403 from provider.
            LLMRateLimitError: 429. Caller may retry with backoff.
            LLMTimeoutError: network / request timeout.
            LLMProviderError: other terminal failure (5xx after retries,
                malformed tool_call JSON, provider-specific errors).
        """

    @abc.abstractmethod
    def available(self) -> bool:
        """True if the API key required for this provider is resolvable."""

    async def close(self) -> None:
        """Release any long-lived client state.

        Default: no-op. SDK clients that pool HTTP connections should
        override and call .aclose().
        """
        return None
