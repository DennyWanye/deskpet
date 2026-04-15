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
        # Test-only injection point; real code leaves this None.
        self._transport: httpx.BaseTransport | None = None

    def _client(self, timeout: float) -> httpx.AsyncClient:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        return httpx.AsyncClient(
            timeout=timeout,
            headers=headers,
            transport=self._transport,
        )

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int = 2048,
    ) -> AsyncIterator[str]:
        temp = temperature if temperature is not None else self.temperature
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
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
