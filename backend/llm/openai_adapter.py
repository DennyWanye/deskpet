"""OpenAI chat-completions adapter with function calling + streaming.

OpenAI-specific quirks handled here:

    1. Function call arguments arrive as a JSON *string*, not a dict.
       We json.loads and raise LLMProviderError on parse failure —
       silently returning `{}` hides real model bugs.

    2. `tool_calls` streaming: the model emits the function name on the
       first delta then drips the JSON arguments across many deltas. We
       must buffer per-index until the delta stops, then parse.

    3. `prompt_tokens_details.cached_tokens` is the new (Oct-2024) prompt
       cache reporting field on gpt-4o — mapped to cache_read_tokens.
       OpenAI has no cache_write billing (free), so cache_write_tokens=0.
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Optional, Union

from llm.base import BaseLLMAdapter
from llm.errors import (
    LLMAuthError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from llm.keys import get_api_key, mask_key
from llm.types import ChatChunk, ChatResponse, ChatUsage, ToolCall

logger = logging.getLogger("deskpet.llm.openai")


class OpenAIAdapter(BaseLLMAdapter):
    """Adapter over the openai SDK (>=1.40)."""

    name = "openai"
    default_model = "gpt-4o"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        default_model: Optional[str] = None,
        timeout: float = 60.0,
        base_url: Optional[str] = None,
    ) -> None:
        self.default_model = default_model or self.default_model
        self.timeout = timeout
        self.base_url = base_url
        self._api_key = api_key or get_api_key("openai")
        self._client: Any = None

    # ───────────────────── lifecycle ─────────────────────

    def available(self) -> bool:
        return bool(self._api_key)

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self._api_key:
            raise LLMAuthError("OPENAI_API_KEY not set", provider=self.name)
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover
            raise LLMProviderError(
                f"openai SDK not installed: {exc}", provider=self.name
            ) from exc
        kwargs: dict[str, Any] = {"api_key": self._api_key, "timeout": self.timeout}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        self._client = AsyncOpenAI(**kwargs)
        logger.debug(
            "openai adapter ready: key=%s model=%s base_url=%s",
            mask_key(self._api_key),
            self.default_model,
            self.base_url or "<default>",
        )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    # ───────────────────── helpers ─────────────────────

    @staticmethod
    def _map_error(exc: Exception) -> Exception:
        status = getattr(exc, "status_code", None)
        provider = "openai"
        name = type(exc).__name__
        if status == 429 or "RateLimit" in name:
            retry_after = None
            resp = getattr(exc, "response", None)
            if resp is not None:
                try:
                    ra = resp.headers.get("retry-after")
                    if ra:
                        retry_after = float(ra)
                except Exception:  # noqa: BLE001
                    pass
            return LLMRateLimitError(str(exc), provider=provider, retry_after=retry_after)
        if status in (401, 403) or "Authentication" in name or "PermissionDenied" in name:
            return LLMAuthError(str(exc), provider=provider)
        if "Timeout" in name or isinstance(exc, TimeoutError):
            return LLMTimeoutError(str(exc), provider=provider)
        return LLMProviderError(str(exc), provider=provider, status_code=status)

    @staticmethod
    def _map_stop_reason(openai_finish: Optional[str]) -> str:
        return {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "function_call": "tool_use",
            "length": "max_tokens",
            "content_filter": "content_filter",
            None: "end_turn",
        }.get(openai_finish, openai_finish or "end_turn")

    @staticmethod
    def _parse_tool_args(raw: Any, *, provider: str = "openai") -> dict[str, Any]:
        """Parse tool_call.function.arguments (JSON string → dict).

        Raise — DON'T silently return {}. Hiding malformed tool calls would
        let the agent dispatch with empty args and produce wrong results.
        """
        if isinstance(raw, dict):
            return raw
        if raw is None or raw == "":
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMProviderError(
                f"{provider} tool_call.arguments not valid JSON: {exc}",
                provider=provider,
            ) from exc
        if not isinstance(parsed, dict):
            raise LLMProviderError(
                f"{provider} tool_call.arguments parsed to non-object",
                provider=provider,
            )
        return parsed

    @staticmethod
    def _usage_from(u: Any) -> ChatUsage:
        if u is None:
            return ChatUsage()
        cached = 0
        details = getattr(u, "prompt_tokens_details", None)
        if details is not None:
            cached = getattr(details, "cached_tokens", 0) or 0
        prompt = getattr(u, "prompt_tokens", 0) or 0
        completion = getattr(u, "completion_tokens", 0) or 0
        # OpenAI bills prompt_tokens inclusive of cached ones, but we track
        # them in separate axes so the budget math isn't double-counting.
        return ChatUsage(
            input_tokens=max(0, prompt - cached),
            output_tokens=completion,
            cache_read_tokens=cached,
            cache_write_tokens=0,
        )

    # ───────────────────── chat ─────────────────────

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        model: Optional[str] = None,
        *,
        stream: bool = False,
        **kwargs: Any,
    ) -> Union[ChatResponse, AsyncIterator[ChatChunk]]:
        client = self._get_client()
        use_model = model or self.default_model

        request: dict[str, Any] = {
            "model": use_model,
            "messages": messages,
        }
        if tools:
            request["tools"] = tools
            request["tool_choice"] = kwargs.get("tool_choice", "auto")
        for k in ("temperature", "top_p", "max_tokens", "stop"):
            if k in kwargs:
                request[k] = kwargs[k]

        if stream:
            request["stream"] = True
            request["stream_options"] = {"include_usage": True}
            return self._stream(client, request, use_model)

        try:
            resp = await client.chat.completions.create(**request)
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc) from exc

        choice = resp.choices[0] if resp.choices else None
        msg = getattr(choice, "message", None) if choice else None
        content = (getattr(msg, "content", None) if msg else None) or ""
        tool_calls: list[ToolCall] = []
        for tc in (getattr(msg, "tool_calls", None) or []) if msg else []:
            fn = getattr(tc, "function", None)
            name = getattr(fn, "name", "") if fn else ""
            raw_args = getattr(fn, "arguments", "") if fn else ""
            tool_calls.append(
                ToolCall(
                    id=getattr(tc, "id", "") or "",
                    name=name,
                    arguments=self._parse_tool_args(raw_args, provider=self.name),
                )
            )

        return ChatResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=self._map_stop_reason(getattr(choice, "finish_reason", None) if choice else None),
            usage=self._usage_from(getattr(resp, "usage", None)),
            model=getattr(resp, "model", use_model) or use_model,
        )

    async def _stream(
        self, client: Any, request: dict[str, Any], model_used: str
    ) -> AsyncIterator[ChatChunk]:
        """Yield ChatChunk from openai stream.

        Tool_call accumulation:
            Each chunk may contain partial deltas for multiple tool_calls
            indexed by `index`. A tool is "complete" when finish_reason
            flips to "tool_calls" (or stream ends) — only then emit
            ToolCall to the consumer.
        """
        tool_buffers: dict[int, dict[str, Any]] = {}
        final_stop: str = "end_turn"
        final_usage = ChatUsage()
        last_model = model_used

        try:
            stream_resp = await client.chat.completions.create(**request)
            async for event in stream_resp:
                last_model = getattr(event, "model", last_model) or last_model
                choices = getattr(event, "choices", None) or []
                if choices:
                    delta = getattr(choices[0], "delta", None)
                    if delta is not None:
                        txt = getattr(delta, "content", None)
                        if txt:
                            yield ChatChunk(delta_content=txt, model=last_model)
                        for tcd in getattr(delta, "tool_calls", None) or []:
                            idx = getattr(tcd, "index", 0) or 0
                            buf = tool_buffers.setdefault(
                                idx, {"id": "", "name": "", "args": ""}
                            )
                            if getattr(tcd, "id", None):
                                buf["id"] = tcd.id
                            fn = getattr(tcd, "function", None)
                            if fn is not None:
                                if getattr(fn, "name", None):
                                    buf["name"] = fn.name
                                if getattr(fn, "arguments", None):
                                    buf["args"] += fn.arguments
                    finish_reason = getattr(choices[0], "finish_reason", None)
                    if finish_reason:
                        final_stop = self._map_stop_reason(finish_reason)
                u = getattr(event, "usage", None)
                if u is not None:
                    final_usage = self._usage_from(u)

            # Flush buffered tool calls now that the stream is complete.
            for idx in sorted(tool_buffers.keys()):
                buf = tool_buffers[idx]
                args = self._parse_tool_args(buf["args"], provider=self.name)
                yield ChatChunk(
                    delta_tool_calls=[
                        ToolCall(id=buf["id"] or f"call_{idx}", name=buf["name"], arguments=args)
                    ],
                    model=last_model,
                )

            yield ChatChunk(
                is_final=True,
                stop_reason=final_stop,
                usage=final_usage,
                model=last_model,
            )
        except Exception as exc:  # noqa: BLE001
            # Don't re-wrap our own LLMProviderError (raised by _parse_tool_args).
            if isinstance(exc, LLMProviderError):
                raise
            raise self._map_error(exc) from exc
