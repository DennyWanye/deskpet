"""Anthropic Claude adapter with prompt-caching support.

Prompt caching strategy (spec requirement `llm-providers.Anthropic Prompt
Caching Integration`):

    1. The *last* block in the `system` list gets `cache_control={"type":
       "ephemeral"}`. Anthropic caches every block up to and including a
       marked block — marking only the last frozen block maximizes hit rate
       while minimizing cache writes (only one breakpoint per system prompt).

    2. When `tools` is passed and stable, we mark the last tool's
       cache_control as well. Stable tool schemas (which is the norm for
       the agent loop) dramatically shrink input_tokens on subsequent turns.

    3. memory_block / dynamic_block goes AFTER the cached boundary so
       per-turn changes don't invalidate cache.

API convention: we accept OpenAI-style messages (`role`: system/user/
assistant/tool) and convert on entry. Reasons:
    - `ToolRegistry.schemas()` outputs OpenAI format; the agent loop
      shouldn't know which provider is downstream.
    - Anthropic's native `tool_result` shape (`{role: user, content:
      [{type: tool_result, tool_use_id, content}]}`) is an internal detail.

Streaming uses anthropic.messages.stream which yields text deltas and
content-block-stop events. tool_use comes as a full block at block_stop.
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

logger = logging.getLogger("deskpet.llm.anthropic")


class AnthropicAdapter(BaseLLMAdapter):
    """Adapter over the anthropic SDK (>=0.40)."""

    name = "anthropic"
    default_model = "claude-sonnet-4-5"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        default_model: Optional[str] = None,
        timeout: float = 60.0,
        max_retries: int = 0,  # Retry at the registry/fallback layer, not SDK.
    ) -> None:
        self.default_model = default_model or self.default_model
        self.timeout = timeout
        self._api_key = api_key or get_api_key("anthropic")
        self._client: Any = None
        self._max_retries = max_retries

    # ───────────────────── lifecycle ─────────────────────

    def available(self) -> bool:
        return bool(self._api_key)

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self._api_key:
            raise LLMAuthError("ANTHROPIC_API_KEY not set", provider=self.name)
        try:
            from anthropic import AsyncAnthropic  # lazy import: heavy SDK
        except ImportError as exc:  # pragma: no cover
            raise LLMProviderError(
                f"anthropic SDK not installed: {exc}", provider=self.name
            ) from exc
        self._client = AsyncAnthropic(
            api_key=self._api_key,
            timeout=self.timeout,
            max_retries=self._max_retries,
        )
        logger.debug(
            "anthropic adapter ready: key=%s model=%s",
            mask_key(self._api_key),
            self.default_model,
        )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:  # noqa: BLE001 - best effort
                pass
            self._client = None

    # ───────────────────── conversion helpers ─────────────────────

    @staticmethod
    def _split_system_messages(
        messages: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Split OpenAI-style messages into anthropic-native (system, chat).

        Multiple system messages are joined as a list of blocks — Anthropic
        accepts `system: [{"type": "text", "text": "..."}]`. The LAST
        block gets cache_control={"type":"ephemeral"} (Requirement §
        llm-providers."Cache breakpoint placed at frozen boundary").
        """
        system_blocks: list[dict[str, Any]] = []
        chat: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            if role == "system":
                content = msg.get("content", "")
                if isinstance(content, str):
                    system_blocks.append({"type": "text", "text": content})
                elif isinstance(content, list):
                    system_blocks.extend(content)
            elif role == "tool":
                # OpenAI tool message -> Anthropic user message with tool_result.
                chat.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.get("tool_call_id", ""),
                                "content": str(msg.get("content", "")),
                            }
                        ],
                    }
                )
            elif role == "assistant" and msg.get("tool_calls"):
                # OpenAI assistant with tool_calls -> Anthropic assistant with
                # tool_use blocks.
                blocks: list[dict[str, Any]] = []
                text = msg.get("content")
                if text:
                    blocks.append({"type": "text", "text": str(text)})
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", tc)
                    args = fn.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args) if args else {}
                        except json.JSONDecodeError:
                            args = {}
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id", ""),
                            "name": fn.get("name", ""),
                            "input": args,
                        }
                    )
                chat.append({"role": "assistant", "content": blocks})
            else:
                chat.append({"role": role, "content": msg.get("content", "")})

        # Mark the last system block as a cache breakpoint.
        if system_blocks:
            last = dict(system_blocks[-1])
            last["cache_control"] = {"type": "ephemeral"}
            system_blocks = [*system_blocks[:-1], last]
        return system_blocks, chat

    @staticmethod
    def _convert_tools(tools: Optional[list[dict[str, Any]]]) -> Optional[list[dict[str, Any]]]:
        """OpenAI function-calling schema → Anthropic tools format."""
        if not tools:
            return None
        out: list[dict[str, Any]] = []
        for t in tools:
            fn = t.get("function", t)
            out.append(
                {
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                }
            )
        # Mark the last tool with cache_control so the (usually stable) tool
        # schema block gets cached too — halves input_tokens in subsequent
        # turns when the tool list doesn't change.
        if out:
            last = dict(out[-1])
            last["cache_control"] = {"type": "ephemeral"}
            out = [*out[:-1], last]
        return out

    @staticmethod
    def _map_error(exc: Exception) -> Exception:
        """Translate anthropic SDK exceptions to our error hierarchy."""
        status = getattr(exc, "status_code", None)
        provider = "anthropic"
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
    def _map_stop_reason(anthropic_reason: Optional[str]) -> str:
        return {
            "end_turn": "end_turn",
            "stop_sequence": "end_turn",
            "tool_use": "tool_use",
            "max_tokens": "max_tokens",
            None: "end_turn",
        }.get(anthropic_reason, anthropic_reason or "end_turn")

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
        system_blocks, chat = self._split_system_messages(messages)
        tool_defs = self._convert_tools(tools)

        request: dict[str, Any] = {
            "model": use_model,
            "messages": chat,
            "max_tokens": kwargs.get("max_tokens", 4096),
        }
        if system_blocks:
            request["system"] = system_blocks
        if tool_defs:
            request["tools"] = tool_defs
        for k in ("temperature", "top_p", "stop_sequences"):
            if k in kwargs:
                request[k] = kwargs[k]

        if stream:
            return self._stream(client, request, use_model)

        try:
            resp = await client.messages.create(**request)
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc) from exc

        return self._build_response(resp, use_model)

    def _build_response(self, resp: Any, model_used: str) -> ChatResponse:
        content_text: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in getattr(resp, "content", []) or []:
            btype = getattr(block, "type", None)
            if btype == "text":
                content_text.append(getattr(block, "text", "") or "")
            elif btype == "tool_use":
                args = getattr(block, "input", None) or {}
                if not isinstance(args, dict):
                    args = {}
                tool_calls.append(
                    ToolCall(
                        id=getattr(block, "id", "") or "",
                        name=getattr(block, "name", "") or "",
                        arguments=args,
                    )
                )

        usage = getattr(resp, "usage", None)
        chat_usage = ChatUsage(
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )

        return ChatResponse(
            content="".join(content_text),
            tool_calls=tool_calls,
            stop_reason=self._map_stop_reason(getattr(resp, "stop_reason", None)),
            usage=chat_usage,
            model=getattr(resp, "model", model_used) or model_used,
        )

    async def _stream(
        self, client: Any, request: dict[str, Any], model_used: str
    ) -> AsyncIterator[ChatChunk]:
        """Yield ChatChunk from anthropic.messages.stream.

        Event model (anthropic SDK):
            content_block_start { type: text | tool_use }
            content_block_delta { delta: {type: text_delta | input_json_delta} }
            content_block_stop
            message_delta { usage, stop_reason }
            message_stop
        """
        try:
            async with client.messages.stream(**request) as stream_ctx:
                pending_tool: Optional[dict[str, Any]] = None
                tool_args_buffer: str = ""
                final_stop_reason = "end_turn"
                final_usage = ChatUsage()
                async for event in stream_ctx:
                    et = getattr(event, "type", None)
                    if et == "content_block_start":
                        block = getattr(event, "content_block", None)
                        btype = getattr(block, "type", None)
                        if btype == "tool_use":
                            pending_tool = {
                                "id": getattr(block, "id", "") or "",
                                "name": getattr(block, "name", "") or "",
                            }
                            tool_args_buffer = ""
                    elif et == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        dtype = getattr(delta, "type", None)
                        if dtype == "text_delta":
                            text_piece = getattr(delta, "text", "") or ""
                            if text_piece:
                                yield ChatChunk(delta_content=text_piece, model=model_used)
                        elif dtype == "input_json_delta" and pending_tool is not None:
                            tool_args_buffer += getattr(delta, "partial_json", "") or ""
                    elif et == "content_block_stop" and pending_tool is not None:
                        try:
                            args_dict = json.loads(tool_args_buffer) if tool_args_buffer else {}
                        except json.JSONDecodeError as exc:
                            raise LLMProviderError(
                                f"anthropic streamed tool args not valid JSON: {exc}",
                                provider=self.name,
                            ) from exc
                        yield ChatChunk(
                            delta_tool_calls=[
                                ToolCall(
                                    id=pending_tool["id"],
                                    name=pending_tool["name"],
                                    arguments=args_dict if isinstance(args_dict, dict) else {},
                                )
                            ],
                            model=model_used,
                        )
                        pending_tool = None
                        tool_args_buffer = ""
                    elif et == "message_delta":
                        sr = getattr(getattr(event, "delta", None), "stop_reason", None)
                        if sr:
                            final_stop_reason = self._map_stop_reason(sr)
                        u = getattr(event, "usage", None)
                        if u is not None:
                            final_usage = ChatUsage(
                                input_tokens=getattr(u, "input_tokens", final_usage.input_tokens),
                                output_tokens=getattr(u, "output_tokens", final_usage.output_tokens),
                                cache_read_tokens=getattr(
                                    u, "cache_read_input_tokens", final_usage.cache_read_tokens
                                ),
                                cache_write_tokens=getattr(
                                    u, "cache_creation_input_tokens", final_usage.cache_write_tokens
                                ),
                            )
                # Final marker chunk carries stop_reason + usage for budget math.
                yield ChatChunk(
                    is_final=True,
                    stop_reason=final_stop_reason,
                    usage=final_usage,
                    model=model_used,
                )
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc) from exc
