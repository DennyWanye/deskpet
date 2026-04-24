"""Unit tests for llm.registry: list_providers, fallback chain, 429 retry."""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Optional, Union

import pytest

from llm.base import BaseLLMAdapter
from llm.budget import DailyBudget
from llm.errors import (
    LLMAuthError,
    LLMBudgetExceededError,
    LLMProviderError,
    LLMRateLimitError,
)
from llm.registry import LLMRegistry
from llm.types import ChatChunk, ChatResponse, ChatUsage, ToolCall


# ───────────────────── stub adapter ─────────────────────


class StubAdapter(BaseLLMAdapter):
    """Programmable adapter — each chat call pops the next behavior from the queue."""

    def __init__(
        self,
        name: str,
        *,
        has_key: bool = True,
        behaviors: Optional[list[Any]] = None,
        default_model: str = "stub-model",
    ) -> None:
        self.name = name
        self._has_key = has_key
        self._behaviors = list(behaviors or [])
        self.default_model = default_model
        self.calls: list[dict[str, Any]] = []

    def available(self) -> bool:
        return self._has_key

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        model: Optional[str] = None,
        *,
        stream: bool = False,
        **kwargs: Any,
    ) -> Union[ChatResponse, AsyncIterator[ChatChunk]]:
        self.calls.append({"messages": messages, "model": model, "kwargs": kwargs})
        if not self._behaviors:
            raise LLMProviderError(f"{self.name}: no programmed behavior", provider=self.name)
        behavior = self._behaviors.pop(0)
        if isinstance(behavior, Exception):
            raise behavior
        if callable(behavior):
            return behavior()
        return behavior


def _ok_response(text: str = "ok", provider_name: str = "stub", model: str = "stub-model") -> ChatResponse:
    return ChatResponse(
        content=text,
        stop_reason="end_turn",
        usage=ChatUsage(input_tokens=10, output_tokens=5),
        model=model,
    )


# ───────────────────── list_providers ─────────────────────


def test_list_providers_skips_missing_key():
    adapters = {
        "anthropic": StubAdapter("anthropic", has_key=False),
        "openai": StubAdapter("openai", has_key=True),
        "gemini": StubAdapter("gemini", has_key=True),
    }
    reg = LLMRegistry(config={}, adapters=adapters)
    providers = reg.list_providers()
    assert "openai" in providers
    assert "gemini" in providers
    assert "anthropic" not in providers


def test_list_providers_all_present():
    adapters = {
        "anthropic": StubAdapter("anthropic"),
        "openai": StubAdapter("openai"),
        "gemini": StubAdapter("gemini"),
    }
    reg = LLMRegistry(config={}, adapters=adapters)
    assert set(reg.list_providers()) == {"anthropic", "openai", "gemini"}


def test_get_raises_on_unknown():
    reg = LLMRegistry(config={}, adapters={"openai": StubAdapter("openai")})
    with pytest.raises(KeyError):
        reg.get("xunfei")


# ───────────────────── fallback chain parsing ─────────────────────


def test_fallback_chain_respects_max_depth():
    reg = LLMRegistry(
        config={
            "main_model": "anthropic:claude-sonnet-4-5",
            "fallback_chain": [
                "openai:gpt-4o",
                "gemini:gemini-1.5-pro",
                "openai:gpt-4o-mini",  # 4th element must be trimmed
            ],
        },
        adapters={n: StubAdapter(n) for n in ("anthropic", "openai", "gemini")},
    )
    chain = reg.fallback_chain()
    assert len(chain) == 3  # primary + 2
    assert chain == [
        "anthropic:claude-sonnet-4-5",
        "openai:gpt-4o",
        "gemini:gemini-1.5-pro",
    ]


def test_fallback_chain_defaults_when_empty():
    reg = LLMRegistry(
        config={},
        adapters={n: StubAdapter(n) for n in ("anthropic", "openai", "gemini")},
    )
    chain = reg.fallback_chain()
    assert chain == [
        "anthropic:claude-sonnet-4-5",
        "openai:gpt-4o",
        "gemini:gemini-1.5-pro",
    ]


# ───────────────────── chat_with_fallback ─────────────────────


@pytest.mark.asyncio
async def test_fallback_primary_503_uses_next_provider():
    primary = StubAdapter(
        "anthropic",
        behaviors=[LLMProviderError("503 Service Unavailable", provider="anthropic", status_code=503)],
    )
    backup = StubAdapter(
        "openai",
        behaviors=[_ok_response("from openai", "openai", "gpt-4o")],
    )
    reg = LLMRegistry(
        config={
            "main_model": "anthropic:claude-sonnet-4-5",
            "fallback_chain": ["openai:gpt-4o", "gemini:gemini-1.5-pro"],
        },
        adapters={
            "anthropic": primary,
            "openai": backup,
            "gemini": StubAdapter("gemini", has_key=False),
        },
    )
    resp = await reg.chat_with_fallback(
        messages=[{"role": "user", "content": "hi"}],
    )
    assert resp.content == "from openai"
    assert resp.model == "gpt-4o"
    assert len(primary.calls) == 1
    assert len(backup.calls) == 1


@pytest.mark.asyncio
async def test_fallback_all_fail_raises_provider_error():
    bad = StubAdapter(
        "anthropic",
        behaviors=[LLMProviderError("gone", provider="anthropic", status_code=500)],
    )
    bad2 = StubAdapter(
        "openai",
        behaviors=[LLMProviderError("also gone", provider="openai", status_code=500)],
    )
    bad3 = StubAdapter(
        "gemini",
        behaviors=[LLMProviderError("gemini down", provider="gemini")],
    )
    reg = LLMRegistry(
        config={
            "main_model": "anthropic:claude-sonnet-4-5",
            "fallback_chain": ["openai:gpt-4o", "gemini:gemini-1.5-pro"],
        },
        adapters={"anthropic": bad, "openai": bad2, "gemini": bad3},
    )
    with pytest.raises(LLMProviderError, match="all providers failed"):
        await reg.chat_with_fallback(messages=[{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_rate_limit_backoff_then_success():
    # Three 429s (hitting retry ceiling of 3) would exhaust retries and move on;
    # two 429s followed by success should stay on the same provider.
    primary = StubAdapter(
        "anthropic",
        behaviors=[
            LLMRateLimitError("slow down", provider="anthropic", retry_after=0.01),
            LLMRateLimitError("slow down", provider="anthropic", retry_after=0.01),
            _ok_response("finally", "anthropic", "claude-sonnet-4-5"),
        ],
    )
    reg = LLMRegistry(
        config={"main_model": "anthropic:claude-sonnet-4-5", "fallback_chain": []},
        adapters={
            "anthropic": primary,
            "openai": StubAdapter("openai", has_key=False),
            "gemini": StubAdapter("gemini", has_key=False),
        },
    )
    resp = await reg.chat_with_fallback(
        messages=[{"role": "user", "content": "hi"}],
        backoff_base=0.01,
    )
    assert resp.content == "finally"
    assert len(primary.calls) == 3  # two 429 + one success


@pytest.mark.asyncio
async def test_rate_limit_exhausts_then_fallback():
    primary = StubAdapter(
        "anthropic",
        behaviors=[
            LLMRateLimitError("slow down", provider="anthropic", retry_after=0.01),
            LLMRateLimitError("slow down", provider="anthropic", retry_after=0.01),
            LLMRateLimitError("slow down", provider="anthropic", retry_after=0.01),
        ],
    )
    backup = StubAdapter(
        "openai",
        behaviors=[_ok_response("backup took over", "openai", "gpt-4o")],
    )
    reg = LLMRegistry(
        config={
            "main_model": "anthropic:claude-sonnet-4-5",
            "fallback_chain": ["openai:gpt-4o"],
        },
        adapters={
            "anthropic": primary,
            "openai": backup,
            "gemini": StubAdapter("gemini", has_key=False),
        },
    )
    resp = await reg.chat_with_fallback(
        messages=[{"role": "user", "content": "hi"}],
        backoff_base=0.01,
    )
    assert resp.content == "backup took over"
    assert len(primary.calls) == 3  # exhausted retry budget
    assert len(backup.calls) == 1


@pytest.mark.asyncio
async def test_auth_error_skips_provider_immediately():
    primary = StubAdapter(
        "anthropic",
        behaviors=[LLMAuthError("bad key", provider="anthropic")],
    )
    backup = StubAdapter(
        "openai",
        behaviors=[_ok_response("backup", "openai", "gpt-4o")],
    )
    reg = LLMRegistry(
        config={
            "main_model": "anthropic:claude-sonnet-4-5",
            "fallback_chain": ["openai:gpt-4o"],
        },
        adapters={
            "anthropic": primary,
            "openai": backup,
            "gemini": StubAdapter("gemini", has_key=False),
        },
    )
    resp = await reg.chat_with_fallback(messages=[{"role": "user", "content": "hi"}])
    # Auth error MUST NOT trigger retry on same provider.
    assert len(primary.calls) == 1
    assert resp.content == "backup"


@pytest.mark.asyncio
async def test_skips_unavailable_providers():
    primary = StubAdapter("anthropic", has_key=False)
    # Primary has no key → move on without a call.
    backup = StubAdapter(
        "openai",
        behaviors=[_ok_response("used openai directly", "openai", "gpt-4o")],
    )
    reg = LLMRegistry(
        config={
            "main_model": "anthropic:claude-sonnet-4-5",
            "fallback_chain": ["openai:gpt-4o"],
        },
        adapters={
            "anthropic": primary,
            "openai": backup,
            "gemini": StubAdapter("gemini", has_key=False),
        },
    )
    resp = await reg.chat_with_fallback(messages=[{"role": "user", "content": "hi"}])
    assert resp.content == "used openai directly"
    assert len(primary.calls) == 0


# ───────────────────── budget integration ─────────────────────


@pytest.mark.asyncio
async def test_budget_blocks_calls_when_exceeded(tmp_path):
    budget = DailyBudget(cap_usd=0.01, state_path=tmp_path / "budget.json")
    # Pre-spend to exceed cap.
    budget.add_usage("openai", "gpt-4o", ChatUsage(output_tokens=100_000))  # $1
    assert budget.check_allowed() is False

    adapter = StubAdapter(
        "anthropic",
        behaviors=[_ok_response("should not be called", "anthropic", "claude-sonnet-4-5")],
    )
    reg = LLMRegistry(
        config={"main_model": "anthropic:claude-sonnet-4-5"},
        adapters={"anthropic": adapter, "openai": StubAdapter("openai", has_key=False), "gemini": StubAdapter("gemini", has_key=False)},
        budget=budget,
    )
    with pytest.raises(LLMBudgetExceededError):
        await reg.chat_with_fallback(messages=[{"role": "user", "content": "hi"}])
    assert len(adapter.calls) == 0


@pytest.mark.asyncio
async def test_budget_records_successful_usage(tmp_path):
    budget = DailyBudget(cap_usd=10.0, state_path=tmp_path / "budget.json")
    resp = _ok_response("hello", "anthropic", "claude-sonnet-4-5")
    resp.usage = ChatUsage(input_tokens=500, output_tokens=200, cache_read_tokens=8000)
    adapter = StubAdapter("anthropic", behaviors=[resp])
    reg = LLMRegistry(
        config={"main_model": "anthropic:claude-sonnet-4-5"},
        adapters={"anthropic": adapter, "openai": StubAdapter("openai", has_key=False), "gemini": StubAdapter("gemini", has_key=False)},
        budget=budget,
    )
    await reg.chat_with_fallback(messages=[{"role": "user", "content": "hi"}])
    assert budget.get_spent() > 0
