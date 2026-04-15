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
    provider._test_transport = transport
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
    provider._test_transport = httpx.MockTransport(handler)
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
    provider._test_transport = httpx.MockTransport(handler)
    assert await provider.health_check() is False


def _sse(frames: list[dict | str]) -> bytes:
    """Serialize OpenAI-style SSE frames. A str entry is treated as raw data (e.g. '[DONE]')."""
    lines: list[str] = []
    for frame in frames:
        if isinstance(frame, str):
            lines.append(f"data: {frame}\n")
        else:
            lines.append(f"data: {json.dumps(frame)}\n")
        lines.append("\n")
    return "".join(lines).encode("utf-8")


def _delta(text: str) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {"content": text}}],
    }


@pytest.mark.asyncio
async def test_chat_stream_yields_tokens_in_order():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        body = _sse([_delta("Hello"), _delta(" "), _delta("world"), "[DONE]"])
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "text/event-stream"},
        )

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="sk-test",
        model="qwen3.6-plus",
    )
    provider._test_transport = httpx.MockTransport(handler)

    tokens: list[str] = []
    async for tok in provider.chat_stream(
        [{"role": "user", "content": "hi"}],
        max_tokens=32,
    ):
        tokens.append(tok)

    assert tokens == ["Hello", " ", "world"]
    assert captured["url"] == "http://example.invalid/v1/chat/completions"
    assert captured["auth"] == "Bearer sk-test"
    assert captured["body"]["model"] == "qwen3.6-plus"
    assert captured["body"]["stream"] is True
    assert captured["body"]["max_tokens"] == 32
    assert captured["body"]["messages"] == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_chat_stream_skips_empty_and_missing_content_deltas():
    """Role-only opening delta and empty content chunks must not emit tokens."""

    def handler(request: httpx.Request) -> httpx.Response:
        frames = [
            # First frame is role-only (no 'content') — OpenAI sends this.
            {
                "choices": [{"index": 0, "delta": {"role": "assistant"}}],
            },
            _delta(""),        # empty string — skip
            _delta("abc"),
            "[DONE]",
        ]
        return httpx.Response(
            200,
            content=_sse(frames),
            headers={"content-type": "text/event-stream"},
        )

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="k",
        model="m",
    )
    provider._test_transport = httpx.MockTransport(handler)

    tokens = [t async for t in provider.chat_stream([{"role": "user", "content": "x"}])]
    assert tokens == ["abc"]


@pytest.mark.asyncio
async def test_chat_stream_respects_explicit_temperature_override():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=_sse(["[DONE]"]),
            headers={"content-type": "text/event-stream"},
        )

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="k",
        model="m",
        temperature=0.7,
    )
    provider._test_transport = httpx.MockTransport(handler)
    async for _ in provider.chat_stream(
        [{"role": "user", "content": "x"}],
        temperature=0.2,
    ):
        pass
    assert captured["body"]["temperature"] == 0.2


@pytest.mark.asyncio
async def test_chat_stream_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="wrong",
        model="m",
    )
    provider._test_transport = httpx.MockTransport(handler)

    with pytest.raises(httpx.HTTPStatusError):
        async for _ in provider.chat_stream([{"role": "user", "content": "x"}]):
            pass


# --------------------------------------------------------------------------
# P2-1-S8 — last_usage capture from the OpenAI stream_options.include_usage
# terminal chunk. The provider must record it for BillingLedger to bill.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_stream_captures_usage():
    """OpenAI/DashScope emit a terminal chunk with `usage` when include_usage
    is set. The provider must stash it in self.last_usage."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        # Shape matches what OpenAI/DashScope actually emit: the last data
        # frame has empty choices and a populated usage.
        frames = [
            _delta("hi"),
            {
                "id": "chatcmpl",
                "object": "chat.completion.chunk",
                "choices": [],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
            "[DONE]",
        ]
        return httpx.Response(
            200,
            content=_sse(frames),
            headers={"content-type": "text/event-stream"},
        )

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="k",
        model="m",
    )
    provider._test_transport = httpx.MockTransport(handler)
    tokens = [t async for t in provider.chat_stream(
        [{"role": "user", "content": "q"}],
    )]
    assert tokens == ["hi"]
    assert provider.last_usage == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }
    # Sanity: include_usage must be in the outgoing body.
    assert captured["body"]["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_chat_stream_last_usage_resets_when_absent():
    """Second call without a usage chunk must leave last_usage=None, not
    reuse the previous stream's usage."""
    calls = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        if calls[0] == 0:
            frames = [
                _delta("a"),
                {
                    "choices": [],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
                "[DONE]",
            ]
        else:
            # Ollama-style: no usage frame at all.
            frames = [_delta("b"), "[DONE]"]
        calls[0] += 1
        return httpx.Response(
            200,
            content=_sse(frames),
            headers={"content-type": "text/event-stream"},
        )

    provider = OpenAICompatibleProvider(
        base_url="http://example.invalid/v1",
        api_key="k",
        model="m",
    )
    provider._test_transport = httpx.MockTransport(handler)
    _ = [t async for t in provider.chat_stream([{"role": "user", "content": "q"}])]
    assert provider.last_usage is not None
    _ = [t async for t in provider.chat_stream([{"role": "user", "content": "q"}])]
    assert provider.last_usage is None


# --------------------------------------------------------------------------
# Integration tests — skipped by default unless the endpoint is reachable.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_ollama_v1_roundtrip():
    """Hits local Ollama's OpenAI-compatible endpoint. Skipped if not running."""
    provider = OpenAICompatibleProvider(
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model=os.environ.get("DESKPET_OLLAMA_MODEL", "gemma4:e4b"),
    )
    if not await provider.health_check():
        pytest.skip("Ollama /v1 not reachable — start ollama or set DESKPET_OLLAMA_MODEL")

    tokens: list[str] = []
    async for tok in provider.chat_stream(
        [{"role": "user", "content": "Reply with the single word: ping"}],
        max_tokens=16,
    ):
        tokens.append(tok)
    joined = "".join(tokens).lower()
    assert "ping" in joined


@pytest.mark.asyncio
async def test_integration_dashscope_roundtrip():
    """Hits DashScope compat-mode endpoint. Skipped if DESKPET_DASHSCOPE_KEY unset."""
    api_key = os.environ.get("DESKPET_DASHSCOPE_KEY")
    if not api_key:
        pytest.skip("DESKPET_DASHSCOPE_KEY not set — skipping live cloud test")

    provider = OpenAICompatibleProvider(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key=api_key,
        model=os.environ.get("DESKPET_DASHSCOPE_MODEL", "qwen3.6-plus"),
    )
    if not await provider.health_check():
        pytest.skip("DashScope /models 非 200 — 可能是密钥无效或网络问题")

    tokens: list[str] = []
    async for tok in provider.chat_stream(
        [{"role": "user", "content": "请用一个字回答：好"}],
        max_tokens=8,
    ):
        tokens.append(tok)
    assert len("".join(tokens)) >= 1
