"""Google Gemini adapter via the `google-genai` SDK.

Gemini diverges from the OpenAI function-calling mental model enough
that we do most of the translation on entry and exit:

    1. Messages: Gemini wants `contents=[Content(role=..., parts=[Part(text=...)])]`
       where `role` is "user" / "model" (not "assistant") and there is no
       `system` role — system prompt goes via `config.system_instruction`.

    2. Tools: OpenAI {type:function, function:{name,description,parameters}}
       → Gemini Tool(function_declarations=[FunctionDeclaration(name,
       description, parameters=Schema(...))]).

    3. Tool calls come back as `response.candidates[0].content.parts[i]
       .function_call` with `{name, args}`. Gemini does NOT generate a
       call id, so we mint one per call (registry callers compare by
       name+args anyway).

    4. Usage: `response.usage_metadata` → prompt_token_count /
       candidates_token_count / cached_content_token_count (only present
       when explicit caching is used; always 0 in MVP).

Gemini context caching requires a minimum of 32,768 tokens and an
explicit CachedContent resource create/delete flow — out of scope for
P4 v0.6.0 MVP. We plumb the field through (as 0) so the ChatUsage
contract remains uniform.
"""
from __future__ import annotations

import json
import logging
import uuid
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

logger = logging.getLogger("deskpet.llm.gemini")


class GeminiAdapter(BaseLLMAdapter):
    """Adapter over google-genai >=1.0."""

    name = "gemini"
    default_model = "gemini-1.5-pro"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        default_model: Optional[str] = None,
        timeout: float = 60.0,
    ) -> None:
        self.default_model = default_model or self.default_model
        self.timeout = timeout
        self._api_key = api_key or get_api_key("gemini")
        self._client: Any = None

    # ───────────────────── lifecycle ─────────────────────

    def available(self) -> bool:
        return bool(self._api_key)

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self._api_key:
            raise LLMAuthError("GEMINI_API_KEY not set", provider=self.name)
        try:
            from google import genai  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover
            raise LLMProviderError(
                f"google-genai SDK not installed: {exc}", provider=self.name
            ) from exc
        self._client = genai.Client(api_key=self._api_key)
        logger.debug(
            "gemini adapter ready: key=%s model=%s",
            mask_key(self._api_key),
            self.default_model,
        )
        return self._client

    # ───────────────────── helpers ─────────────────────

    @staticmethod
    def _map_error(exc: Exception) -> Exception:
        msg = str(exc)
        name = type(exc).__name__
        lower = msg.lower()
        provider = "gemini"
        # google-genai surfaces structured errors; fall back to string matching.
        if "429" in msg or "resource_exhausted" in lower or "rate" in lower:
            return LLMRateLimitError(msg, provider=provider)
        if "401" in msg or "403" in msg or "permission" in lower or "unauthenticated" in lower:
            return LLMAuthError(msg, provider=provider)
        if "timeout" in lower or isinstance(exc, TimeoutError) or "deadline" in lower:
            return LLMTimeoutError(msg, provider=provider)
        return LLMProviderError(msg, provider=provider, status_code=None)

    @staticmethod
    def _split_system(
        messages: list[dict[str, Any]],
    ) -> tuple[Optional[str], list[dict[str, Any]]]:
        system_parts: list[str] = []
        chat: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            if role == "system":
                c = msg.get("content", "")
                if isinstance(c, str):
                    system_parts.append(c)
                elif isinstance(c, list):
                    for b in c:
                        if isinstance(b, dict) and b.get("type") == "text":
                            system_parts.append(str(b.get("text", "")))
            else:
                chat.append(msg)
        sys_text = "\n\n".join(s for s in system_parts if s) or None
        return sys_text, chat

    @staticmethod
    def _build_contents(messages: list[dict[str, Any]]) -> list[Any]:
        """OpenAI messages → Gemini Content list.

        Mapping:
            user      → Content(role='user', parts=[Part(text=...)])
            assistant → Content(role='model', parts=[Part(text=...)])
              + any tool_calls → Part(function_call=FunctionCall(name,args))
            tool      → Content(role='user', parts=[Part(
                             function_response=FunctionResponse(name, response))])
        """
        from google.genai import types as gt  # type: ignore[import-untyped]

        contents: list[Any] = []
        for msg in messages:
            role = msg.get("role")
            if role == "user":
                c = msg.get("content", "")
                text = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
                contents.append(gt.Content(role="user", parts=[gt.Part(text=text)]))
            elif role == "assistant":
                parts: list[Any] = []
                if msg.get("content"):
                    parts.append(gt.Part(text=str(msg["content"])))
                for tc in msg.get("tool_calls") or []:
                    fn = tc.get("function", tc)
                    args = fn.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args) if args else {}
                        except json.JSONDecodeError:
                            args = {}
                    parts.append(
                        gt.Part(
                            function_call=gt.FunctionCall(
                                name=fn.get("name", ""),
                                args=args if isinstance(args, dict) else {},
                            )
                        )
                    )
                if not parts:
                    parts.append(gt.Part(text=""))
                contents.append(gt.Content(role="model", parts=parts))
            elif role == "tool":
                contents.append(
                    gt.Content(
                        role="user",
                        parts=[
                            gt.Part(
                                function_response=gt.FunctionResponse(
                                    name=msg.get("name", ""),
                                    response={"content": str(msg.get("content", ""))},
                                )
                            )
                        ],
                    )
                )
        return contents

    @staticmethod
    def _convert_tools(tools: Optional[list[dict[str, Any]]]) -> Optional[list[Any]]:
        """OpenAI tools list → [Tool(function_declarations=[...])]."""
        if not tools:
            return None
        from google.genai import types as gt  # type: ignore[import-untyped]

        decls: list[Any] = []
        for t in tools:
            fn = t.get("function", t)
            decls.append(
                gt.FunctionDeclaration(
                    name=fn.get("name", ""),
                    description=fn.get("description", ""),
                    parameters=fn.get("parameters") or {"type": "object", "properties": {}},
                )
            )
        return [gt.Tool(function_declarations=decls)]

    @staticmethod
    def _usage_from(meta: Any) -> ChatUsage:
        if meta is None:
            return ChatUsage()
        prompt = getattr(meta, "prompt_token_count", 0) or 0
        completion = getattr(meta, "candidates_token_count", 0) or 0
        cached = getattr(meta, "cached_content_token_count", 0) or 0
        return ChatUsage(
            input_tokens=max(0, prompt - cached),
            output_tokens=completion,
            cache_read_tokens=cached,
            cache_write_tokens=0,
        )

    @staticmethod
    def _map_finish_reason(fr: Any) -> str:
        if fr is None:
            return "end_turn"
        name = getattr(fr, "name", None) or str(fr)
        mapping = {
            "STOP": "end_turn",
            "MAX_TOKENS": "max_tokens",
            "SAFETY": "content_filter",
            "RECITATION": "content_filter",
            "OTHER": "end_turn",
            "TOOL_USE": "tool_use",
        }
        return mapping.get(name, "end_turn")

    @staticmethod
    def _fresh_call_id() -> str:
        return f"gemini_{uuid.uuid4().hex[:10]}"

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
        system_instruction, chat_messages = self._split_system(messages)
        contents = self._build_contents(chat_messages)
        tool_list = self._convert_tools(tools)

        from google.genai import types as gt  # type: ignore[import-untyped]

        cfg_kwargs: dict[str, Any] = {}
        if system_instruction:
            cfg_kwargs["system_instruction"] = system_instruction
        if tool_list:
            cfg_kwargs["tools"] = tool_list
        for k in ("temperature", "top_p", "max_output_tokens"):
            if k in kwargs:
                cfg_kwargs[k] = kwargs[k]
        # Support `max_tokens` alias for parity with Anthropic / OpenAI.
        if "max_tokens" in kwargs and "max_output_tokens" not in cfg_kwargs:
            cfg_kwargs["max_output_tokens"] = kwargs["max_tokens"]
        config = gt.GenerateContentConfig(**cfg_kwargs) if cfg_kwargs else None

        if stream:
            return self._stream(client, use_model, contents, config)

        try:
            response = await client.aio.models.generate_content(
                model=use_model,
                contents=contents,
                config=config,
            )
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc) from exc

        return self._build_response(response, use_model)

    def _build_response(self, response: Any, model_used: str) -> ChatResponse:
        content_text: list[str] = []
        tool_calls: list[ToolCall] = []
        finish_reason = None
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            cand = candidates[0]
            finish_reason = getattr(cand, "finish_reason", None)
            for part in getattr(getattr(cand, "content", None), "parts", None) or []:
                txt = getattr(part, "text", None)
                if txt:
                    content_text.append(txt)
                fc = getattr(part, "function_call", None)
                if fc is not None:
                    args = getattr(fc, "args", None) or {}
                    # fc.args can be a proto Map type — coerce to plain dict
                    try:
                        args_dict = dict(args) if not isinstance(args, dict) else args
                    except Exception:  # noqa: BLE001
                        args_dict = {}
                    tool_calls.append(
                        ToolCall(
                            id=self._fresh_call_id(),
                            name=getattr(fc, "name", "") or "",
                            arguments=args_dict,
                        )
                    )

        stop_reason = "tool_use" if tool_calls else self._map_finish_reason(finish_reason)
        return ChatResponse(
            content="".join(content_text),
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=self._usage_from(getattr(response, "usage_metadata", None)),
            model=getattr(response, "model_version", None) or model_used,
        )

    async def _stream(
        self, client: Any, model_used: str, contents: list[Any], config: Any
    ) -> AsyncIterator[ChatChunk]:
        final_usage = ChatUsage()
        final_stop = "end_turn"
        emitted_tool_calls = False
        last_model = model_used

        try:
            stream = await client.aio.models.generate_content_stream(
                model=model_used,
                contents=contents,
                config=config,
            )
            async for chunk in stream:
                last_model = getattr(chunk, "model_version", None) or last_model
                candidates = getattr(chunk, "candidates", None) or []
                if candidates:
                    cand = candidates[0]
                    fr = getattr(cand, "finish_reason", None)
                    if fr is not None:
                        final_stop = self._map_finish_reason(fr)
                    for part in getattr(getattr(cand, "content", None), "parts", None) or []:
                        txt = getattr(part, "text", None)
                        if txt:
                            yield ChatChunk(delta_content=txt, model=last_model)
                        fc = getattr(part, "function_call", None)
                        if fc is not None:
                            args = getattr(fc, "args", None) or {}
                            try:
                                args_dict = dict(args) if not isinstance(args, dict) else args
                            except Exception:  # noqa: BLE001
                                args_dict = {}
                            emitted_tool_calls = True
                            yield ChatChunk(
                                delta_tool_calls=[
                                    ToolCall(
                                        id=self._fresh_call_id(),
                                        name=getattr(fc, "name", "") or "",
                                        arguments=args_dict,
                                    )
                                ],
                                model=last_model,
                            )
                u = getattr(chunk, "usage_metadata", None)
                if u is not None:
                    final_usage = self._usage_from(u)

            if emitted_tool_calls and final_stop != "tool_use":
                final_stop = "tool_use"
            yield ChatChunk(
                is_final=True,
                stop_reason=final_stop,
                usage=final_usage,
                model=last_model,
            )
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc) from exc
