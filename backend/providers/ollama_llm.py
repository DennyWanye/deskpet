from __future__ import annotations

import json
from typing import AsyncIterator

import httpx
import structlog

logger = structlog.get_logger()


class OllamaLLM:
    """Ollama LLM provider with async streaming chat via the Ollama HTTP API."""

    def __init__(
        self,
        model: str = "qwen2.5:14b",
        base_url: str = "http://localhost:11434",
        temperature: float = 0.7,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature

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
            "options": {"temperature": temp, "num_predict": max_tokens},
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST", f"{self.base_url}/api/chat", json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if "message" in data and "content" in data["message"]:
                        token = data["message"]["content"]
                        if token:
                            yield token
                    if data.get("done", False):
                        break

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                if resp.status_code != 200:
                    return False
                models = resp.json().get("models", [])
                return any(m["name"].startswith(self.model) for m in models)
        except Exception:
            return False
