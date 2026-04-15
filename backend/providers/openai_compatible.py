from __future__ import annotations

import json
from typing import AsyncIterator

import httpx
import structlog

logger = structlog.get_logger()


class OpenAICompatibleProvider:
    """LLM provider speaking OpenAI's /v1/chat/completions SSE protocol.

    Works against any compatible endpoint:
      - Local Ollama on /v1 (api_key "ollama", ignored server-side).
      - DashScope compatible-mode /v1 (real bearer token).
      - Any other OpenAI-compatible gateway.

    Implements the `LLMProvider` Protocol in providers/base.py.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.7,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        # P2-1-S8: last completed stream's usage block from the OpenAI SSE
        # protocol (populated only when the server emits one — OpenAI/DashScope
        # always do when stream_options.include_usage=True; Ollama today does
        # NOT emit usage in its SSE stream, so this stays None after Ollama
        # calls and billing records nothing. main.py handles that case.
        self.last_usage: dict | None = None
        # Test-only injection: unit tests assign an httpx.MockTransport here.
        # Production code MUST leave this None; otherwise every request goes
        # through the mock and never reaches the real endpoint.
        self._test_transport: httpx.BaseTransport | None = None

    def _client(self, timeout: float) -> httpx.AsyncClient:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        return httpx.AsyncClient(
            timeout=timeout,
            headers=headers,
            transport=self._test_transport,
        )

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int = 2048,
    ) -> AsyncIterator[str]:
        temp = temperature if temperature is not None else self.temperature
        # P2-1-S8: reset per-call so stale data from the previous stream
        # never leaks into billing when the current stream carries no usage.
        self.last_usage = None
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            # S8: ask OpenAI-compat servers to emit a terminal chunk with
            # a `usage` field so BillingLedger can record prompt/completion
            # tokens. Harmless on servers that ignore it (Ollama).
            "stream_options": {"include_usage": True},
            "temperature": temp,
            "max_tokens": max_tokens,
        }
        async with self._client(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[len("data:"):].strip()
                    if not data_str:
                        continue
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        logger.warning(
                            "openai_compat_bad_sse_frame", raw=data_str
                        )
                        continue
                    # P2-1-S8: the usage chunk typically arrives as the
                    # terminal frame (choices=[], usage={...}). Capture it
                    # regardless of whether choices is empty.
                    usage = data.get("usage")
                    if usage:
                        self.last_usage = usage
                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    token = delta.get("content")
                    if token:
                        yield token

    async def health_check(self) -> bool:
        try:
            async with self._client(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/models")
                return resp.status_code == 200
        except Exception:
            return False
