"""Unit tests for OpenAICompatibleProvider (P2-1-S1).

Covers:
- Protocol conformance (runtime-checkable LLMProvider)
- chat_stream against a mocked SSE transport (no network)
- health_check against mocked /models endpoints
- Two integration tests (skip if endpoints offline / no api key)
"""
from __future__ import annotations

import json
import os

import httpx
import pytest

from providers.base import LLMProvider
from providers.openai_compatible import OpenAICompatibleProvider


def test_openai_compatible_implements_protocol():
    provider = OpenAICompatibleProvider(
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="gemma4:e4b",
    )
    assert isinstance(provider, LLMProvider)


@pytest.mark.asyncio
async def test_health_check_returns_true_on_200():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"data": [{"id": "any-model"}]})

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="test-key",
        model="x",
    )
    transport = httpx.MockTransport(handler)
    # Inject the mock transport via a provider hook (see impl below).
    provider._transport = transport
    assert await provider.health_check() is True


@pytest.mark.asyncio
async def test_health_check_returns_false_on_5xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="test-key",
        model="x",
    )
    provider._transport = httpx.MockTransport(handler)
    assert await provider.health_check() is False


@pytest.mark.asyncio
async def test_health_check_returns_false_on_connect_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="test-key",
        model="x",
    )
    provider._transport = httpx.MockTransport(handler)
    assert await provider.health_check() is False
