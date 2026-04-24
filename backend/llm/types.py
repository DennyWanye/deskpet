"""Normalized LLM response data model.

Each provider adapter MUST map its native response into these pydantic
models so the agent loop never has to branch on provider-specific shapes.

Usage fields deliberately include cache_read_tokens / cache_write_tokens
as first-class: Anthropic prompt caching (the reason we pay the sticker
price for Claude) reports them, OpenAI exposes `cached_tokens` on gpt-4o,
and Gemini reports 0 on both (context caching only in paid tier). The
caller MUST NOT assume non-Anthropic adapters zero-out these fields by
accident — registry tests enforce all 4 fields present.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ChatUsage(BaseModel):
    """Token accounting returned alongside each ChatResponse.

    Four fields kept even for providers that don't support caching so
    downstream budget math stays uniform: sum(input_tokens, output_tokens,
    cache_read_tokens, cache_write_tokens) should equal total billed tokens
    on the provider's invoice (cache_read usually discounted).
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class ToolCall(BaseModel):
    """Normalized function-calling request from the model.

    `arguments` MUST be a parsed dict. Anthropic returns dicts natively;
    OpenAI returns JSON strings that adapters MUST json.loads (and raise
    LLMProviderError on parse failure — silently returning empty arguments
    leads to tool dispatch hangs that are painful to debug).
    """

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    """Single-turn completion result.

    stop_reason values: "end_turn" | "tool_use" | "max_tokens" | "error" |
    "content_filter" (OpenAI). agent loop branches on "tool_use" to dispatch
    tools; everything else terminates the turn.
    """

    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: str = ""
    usage: ChatUsage = Field(default_factory=ChatUsage)
    model: str = ""


class ChatChunk(BaseModel):
    """Streaming delta. Emitted by adapter.chat(stream=True).

    Contract:
        - `delta_content` is the *incremental* text since last chunk
          (not the cumulative buffer). Consumer concatenates.
        - `delta_tool_calls` populated only when the stream yields a
          *complete* tool_call (arguments fully parsed); partial tool
          call JSON buffering is the adapter's job.
        - `is_final=True` marks the last chunk; `usage` is attached then.
    """

    delta_content: str = ""
    delta_tool_calls: list[ToolCall] = Field(default_factory=list)
    is_final: bool = False
    usage: Optional[ChatUsage] = None
    stop_reason: str = ""
    model: str = ""
