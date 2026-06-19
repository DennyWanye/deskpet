# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S20 Wave 2b — thin LLM shim for AgentLoop.

AgentLoop expects a ``chat_with_fallback(messages, tools=, ...)``
returning an ``llm.types.ChatResponse``. The deskpet runtime currently
uses ``OpenAICompatibleProvider`` (which does ``chat_stream`` for the
chat panel, plus the new ``chat_with_tools`` non-streaming method).

This shim wires the two together so we can drive the new tool-use loop
without spinning up the full ``LLMRegistry`` (which would require its
own anthropic/openai/gemini API keys).

P4-S25 A1: also exposes ``chat_with_fallback_stream`` for the streaming
path. Same return shape (ChatResponse on completion) but yields
intermediate delta events the agent loop can forward to the WS so the
user sees text/tool calls trickle in instead of waiting silently for
30+ seconds on thinking-mode models.

Production wiring:
    shim = OpenAICompatibleAgentLLM(provider=cloud_or_local_provider)
    loop = AgentLoop(llm_registry=shim, tool_registry=registry_v2)
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from llm.types import ChatResponse, ChatUsage, ToolCall


class OpenAICompatibleAgentLLM:
    """Adapter: ``OpenAICompatibleProvider`` → AgentLoop LLM protocol."""

    def __init__(self, provider) -> None:  # type: ignore[no-untyped-def]
        self._provider = provider

    async def chat_with_fallback(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        # ``model`` is ignored — provider already locked to a model at
        # construction time. (The agent loop passes ``model=None`` by
        # default, and the upstream chat handler can swap providers
        # rather than re-binding model on the fly.)
        max_tokens = int(kwargs.get("max_tokens", 2048))
        temperature = kwargs.get("temperature")
        # P4-S25: structured output pass-through (response_format), so
        # callers like the plan-mode phase can demand JSON schema.
        response_format = kwargs.get("response_format")
        raw = await self._provider.chat_with_tools(
            messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,
        )
        return _raw_to_response(raw)

    async def chat_with_fallback_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict]:
        """P4-S25 A1: streaming variant — yields delta + final events.

        Events forwarded straight from
        :meth:`OpenAICompatibleProvider.chat_stream_with_tools`. The
        agent loop converts them into AssistantDeltaEvent and an
        AssistantMessageEvent at end. The shape:

            {"type": "delta", "content": str}
            {"type": "delta_reasoning", "content": str}
            {"type": "final", "content", "reasoning_content",
                              "tool_calls", "stop_reason", "model", "usage"}
        """
        max_tokens = int(kwargs.get("max_tokens", 2048))
        temperature = kwargs.get("temperature")
        response_format = kwargs.get("response_format")
        # 2026-06-06 真机：中转 relay 经代理（Clash Verge 等）间歇掉**流式连接**，
        # httpx 抛 ReadError/ConnectError → 整个 agent turn 立即崩（这是真机 TC-5.3
        # 等多工具任务跑不到 ≥5 工具的根因）。加重试：流在**产出任何事件前**掉链
        # （典型代理在连接建立/首字节阶段掉链）→ 干净重试整个流（带 backoff）。
        # 已 yield 过事件则不重试（避免前端 MessageStream delta 重复），抛给上层
        # ErrorEvent + 方案 B codify 处理。
        import asyncio as _aio
        _max_retries = 3
        for _attempt in range(1, _max_retries + 1):
            _yielded_any = False
            try:
                async for ev in self._provider.chat_stream_with_tools(
                    messages,
                    tools=tools,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    response_format=response_format,
                ):
                    _yielded_any = True
                    yield ev
                return  # 流正常完成
            except Exception as exc:  # noqa: BLE001
                _name = type(exc).__name__
                _transient = (
                    "Timeout" in _name
                    or _name in (
                        "ReadError", "ConnectError", "RemoteProtocolError",
                        "ProtocolError", "ConnectionError", "APIConnectionError",
                        "APITimeoutError", "WriteError", "PoolTimeout",
                    )
                    or isinstance(exc, (TimeoutError, ConnectionError))
                )
                if _yielded_any or not _transient or _attempt >= _max_retries:
                    raise
                await _aio.sleep(0.5 * (2 ** (_attempt - 1)))
                # 重入循环重新 stream


def _raw_to_response(raw: dict) -> ChatResponse:
    """Translate provider's chat_with_tools dict into a ChatResponse."""
    usage = raw.get("usage") or {}
    return ChatResponse(
        content=raw.get("content", "") or "",
        reasoning_content=raw.get("reasoning_content", "") or "",
        tool_calls=[
            ToolCall(
                id=tc.get("id", "") or "",
                name=tc.get("name", "") or "",
                arguments=tc.get("arguments", {}) or {},
                # P5-S2: forward malformed-args metadata if present so
                # AgentLoop can short-circuit dispatch with a useful
                # error message back to the model.
                args_parse_error=tc.get("_args_parse_error"),
                args_raw=tc.get("_args_raw"),
            )
            for tc in (raw.get("tool_calls") or [])
        ],
        stop_reason=raw.get("stop_reason", "end_turn"),
        model=raw.get("model", ""),
        usage=ChatUsage(
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            cache_read_tokens=int(
                (usage.get("prompt_tokens_details") or {}).get(
                    "cached_tokens"
                )
                or 0
            ),
            cache_write_tokens=0,
        ),
    )
