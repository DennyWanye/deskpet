"""P4-S6 LLM provider abstraction (Anthropic / OpenAI / Gemini).

Layered:
    types.py        ChatResponse / ChatChunk / ToolCall / ChatUsage dataclasses
    errors.py       LLMProviderError + 429 / timeout / auth / budget subclasses
    base.py         BaseLLMAdapter abstract + shared helpers
    anthropic_adapter.py   prompt-caching aware, streaming
    openai_adapter.py      function-calling tool_calls, streaming
    gemini_adapter.py      google-genai Tool/FunctionDeclaration conversion
    registry.py     LLMRegistry + fallback chain (primary + 2 backups)
    budget.py       DailyBudget with USD cap + warning thresholds
    pricing.py      per-model per-1M-token USD price table
    keys.py         env > keyring resolution + mask_key helper

Why a new top-level package instead of reusing backend/providers/:
    backend/providers/ holds ASR/TTS/VAD provider adapters + the
    OpenAI-compatible *ollama* client used by the P3 HybridRouter. Those
    assume a single base_url and a single vendor protocol. P4 introduces
    a multi-provider cloud LLM layer (Anthropic prompt caching, OpenAI
    function calling, Gemini FunctionDeclaration) with fallback chain and
    budget cap — that's a separate concern and a separate package.
"""
from llm.errors import (
    LLMAuthError,
    LLMBudgetExceededError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from llm.types import ChatChunk, ChatResponse, ChatUsage, ToolCall

__all__ = [
    "ChatChunk",
    "ChatResponse",
    "ChatUsage",
    "ToolCall",
    "LLMAuthError",
    "LLMBudgetExceededError",
    "LLMProviderError",
    "LLMRateLimitError",
    "LLMTimeoutError",
]
