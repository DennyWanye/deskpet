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
                # Log the upstream's self-reported identity on the first SSE
                # frame. The `model`/`id`/`system_fingerprint` come straight
                # from the server, so this is unforgeable proof of which
                # endpoint actually answered — invaluable when debugging
                # routing between local/cloud providers that use the same
                # wire protocol. Debug-level: off by default, opt-in via
                # DESKPET_LOG_LEVEL=DEBUG.
                _dumped_server_id = False
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
                    if not _dumped_server_id:
                        logger.debug(
                            "provider_response_identity",
                            url=f"{self.base_url}/chat/completions",
                            configured_model=self.model,
                            server_model=data.get("model"),
                            server_id=data.get("id"),
                            system_fingerprint=data.get("system_fingerprint"),
                        )
                        _dumped_server_id = True
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

    async def chat_with_tools(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        max_tokens: int = 2048,
        temperature: float | None = None,
    ) -> dict:
        """P4-S20: non-streaming chat with optional tool_calls.

        Returns the parsed OpenAI choice payload:
            {
              "content": str,
              "tool_calls": [{id, name, arguments(dict)}],
              "stop_reason": "end_turn" | "tool_use" | "max_tokens" | "error",
              "model": str,
              "usage": {input_tokens, output_tokens, ...},
            }

        Tool_calls' ``arguments`` are pre-parsed from the JSON string the
        OpenAI protocol returns; agent loop can dispatch directly.
        """
        temp = temperature if temperature is not None else self.temperature
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "temperature": temp,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        async with self._client(timeout=self.timeout) as client:
            r = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
        choices = data.get("choices") or []
        if not choices:
            return {
                "content": "",
                "tool_calls": [],
                "stop_reason": "error",
                "model": data.get("model", self.model),
                "usage": data.get("usage") or {},
            }
        c0 = choices[0]
        msg = c0.get("message") or {}
        finish = c0.get("finish_reason", "stop")
        # OpenAI returns finish_reason="tool_calls" when tools were called.
        stop_reason = (
            "tool_use"
            if finish == "tool_calls" or msg.get("tool_calls")
            else ("end_turn" if finish == "stop" else finish)
        )
        tcs_raw = msg.get("tool_calls") or []
        tcs: list[dict] = []
        for tc in tcs_raw:
            fn = tc.get("function") or {}
            args_raw = fn.get("arguments", "{}")
            try:
                args = (
                    json.loads(args_raw)
                    if isinstance(args_raw, str)
                    else dict(args_raw or {})
                )
            except (json.JSONDecodeError, TypeError):
                args = {}
            tcs.append(
                {
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "arguments": args,
                }
            )
        usage = data.get("usage") or {}
        # Stash so billing.ledger can debit (matches stream behavior).
        self.last_usage = usage
        return {
            "content": msg.get("content") or "",
            "tool_calls": tcs,
            "stop_reason": stop_reason,
            "model": data.get("model", self.model),
            "usage": usage,
        }

    async def health_check(self) -> bool:
        try:
            # 15s timeout: Sealos scale-to-zero cold start can exceed 5s,
            # causing health_check to return False and cloud_first to
            # silently fall back to local for the next 30s (cache TTL).
            async with self._client(timeout=15.0) as client:
                # Primary probe: GET /models (OpenAI standard, cheap, no token cost).
                resp = await client.get(f"{self.base_url}/models")
                if resp.status_code == 200:
                    return True

                # Fallback: many third-party OpenAI-compatible relays
                # (chinzy.com, some sealos endpoints, certain proxies)
                # only implement /chat/completions and return 404/501 on
                # /models. Try a 1-token chat probe so users can still use
                # those services. Costs ~prompt_tokens charge but proves
                # the key + model are valid.
                if resp.status_code in (404, 405, 501):
                    chat_resp = await client.post(
                        f"{self.base_url}/chat/completions",
                        json={
                            "model": self.model,
                            "messages": [{"role": "user", "content": "."}],
                            "max_tokens": 1,
                            "temperature": 0,
                        },
                    )
                    return chat_resp.status_code == 200
                return False
        except Exception:
            return False
