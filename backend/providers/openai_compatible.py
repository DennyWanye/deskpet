from __future__ import annotations

import json
from typing import AsyncIterator

import httpx
import structlog

from llm.errors import LLMProviderError

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
        timeout: float | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        # P4-S25 / P5-S2: timeout is a tuple-style budget instead of a single
        # number:
        #   * connect = 10s — TCP + TLS handshake. Bumped 5→10s on 2026-05-11
        #                     after seeing real ConnectTimeout failures while
        #                     chinzy itself reported no errors (their server
        #                     never saw the request — handshake failed our
        #                     side). Windows DNS cache invalidation + TLS
        #                     slow-start on cold connections legitimately
        #                     takes 6-8s. 10s gives headroom without making
        #                     real outages drag.
        #   * read    = 60s — between bytes / for a full single response.
        #                     Initial 20s based on user-reported 14.4s p-max
        #                     was too tight: 2026-05-09 logs showed frequent
        #                     `attempt=1 of=3 ReadTimeout` retries that ate
        #                     the whole budget on a string of slow turns.
        #                     60s covers thinking-mode bursts + chinzy
        #                     idle-spikes; the proxy's own 100-120s idle cut
        #                     remains the outer bound.
        #   * write   = 5s  — POST body is small; anything past this is dead.
        #   * pool    = 5s  — connection-pool acquisition.
        # Caller can still pass a scalar/Timeout; we honour that verbatim.
        # P5-S2 F1 (2026-05-12) — bumped read 60→120 and write 5→10 per
        # chinzy's integration guide (docs/中转站建议.md). chinzy's PR #27
        # ships a 15s SSE keep-alive comment that resets read timers on
        # every hop, so a 120s read budget is generous + safe and covers
        # individual reasoning tokens that can be 5KB+ on slow chunks.
        # connect stays at 10s — Windows DNS slow-start legitimately
        # takes 6-8s, chinzy's suggested 5s would false-fail occasionally.
        if timeout is None:
            self.timeout: float | httpx.Timeout = httpx.Timeout(
                connect=10.0, read=120.0, write=10.0, pool=5.0,
            )
        else:
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

    def _client(self, timeout: float | httpx.Timeout) -> httpx.AsyncClient:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # P5-S2 F1 (2026-05-12) — chinzy integration guide root cause for
        # "succeeds then next request ConnectError" (docs/中转站建议.md
        # Failure Mode 2):
        #
        # httpx's default connection pool retains the TCP socket between
        # requests for reuse. Middleboxes (NAT, corporate proxy, ISP) and
        # the server's own keepAliveTimeout=75s silently kill that idle
        # socket. When we send the NEXT request to the stale FD, the
        # first write gets ECONNRESET and httpx surfaces it as
        # ConnectError — looking exactly like "can't connect to chinzy"
        # even though chinzy is healthy.
        #
        # Fix per chinzy guide: disable pool reuse entirely. New TCP +
        # TLS handshake every request costs ~50ms; for an LLM workload
        # where each call is 5-30s, the overhead is irrelevant and the
        # fix eliminates an entire class of intermittent failures.
        #
        # transport retries=1 adds one implicit retry on raw socket
        # errors (httpx layer, BEFORE our 3-retry application backoff).
        # Cheap belt-and-suspenders for the (rare) case where a fresh
        # connection still hits a transient SYN drop.
        limits = httpx.Limits(max_keepalive_connections=0)
        # Test path: when a MockTransport is injected we don't apply
        # AsyncHTTPTransport(retries=1) — mock transports don't honour
        # retries and the tests assert exact request counts.
        if self._test_transport is not None:
            transport = self._test_transport
        else:
            transport = httpx.AsyncHTTPTransport(retries=1)
        return httpx.AsyncClient(
            timeout=timeout,
            headers=headers,
            limits=limits,
            transport=transport,
        )

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int = 8192,
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
        max_tokens: int = 8192,
        temperature: float | None = None,
        response_format: dict | None = None,
    ) -> dict:
        """P4-S25 fix: HTTP-stream-as-transport, response-as-aggregate.

        Public contract: returns the same dict shape callers always saw —
            {content, reasoning_content, tool_calls, stop_reason, model, usage}

        Internal: delegates to :meth:`chat_stream_with_tools` so the HTTP
        request is ALWAYS ``stream: True``. We discard delta events and
        return only the final aggregate. This fixes the recurring chinzy
        ``RemoteProtocolError("Server disconnected without sending a
        response")`` — proxies kill idle ``stream: False`` requests while
        thinking-mode models (deepseek-v4-pro etc.) are still reasoning
        for 30+ seconds, but happily forward SSE the moment tokens start
        flowing. So even callers that don't care about deltas (plan
        extractor, agent_loop's non-stream fallback, ad-hoc tools) now
        ride the streaming transport for reliability.

        Retry / reasoning_content-400 / response_format / cache_control
        handling is centralised inside ``chat_stream_with_tools`` — this
        wrapper intentionally has no error-handling logic of its own.
        """
        final_dict: dict | None = None
        async for ev in self.chat_stream_with_tools(
            messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,
        ):
            if ev.get("type") == "final":
                final_dict = ev
            # delta / delta_reasoning events are intentionally ignored —
            # callers of chat_with_tools want a single dict, not a stream.
        if final_dict is None:
            # chat_stream_with_tools always yields a final event on
            # success (and raises on failure). If we somehow got here
            # without one, surface a stable error.
            raise LLMProviderError("LLM 调用失败 (stream 未返回 final 事件)。")
        return {
            "content": final_dict.get("content") or "",
            "reasoning_content": final_dict.get("reasoning_content") or "",
            "tool_calls": final_dict.get("tool_calls") or [],
            "stop_reason": final_dict.get("stop_reason", "end_turn"),
            "model": final_dict.get("model", self.model),
            "usage": final_dict.get("usage") or {},
        }

    async def _legacy_chat_with_tools_nonstream(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        max_tokens: int = 8192,
        temperature: float | None = None,
        response_format: dict | None = None,
    ) -> dict:
        """DEPRECATED: original ``stream: False`` implementation.

        Preserved temporarily in case we need to A/B compare against
        the streaming-as-transport path. Not wired anywhere — callers
        go through :meth:`chat_with_tools` which now streams.

        The original docstring follows.

        P4-S20: non-streaming chat with optional tool_calls.

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

        ``response_format`` (P4-S25): OpenAI structured-output spec, e.g.
        ``{"type": "json_schema", "json_schema": {...}}`` or the simpler
        ``{"type": "json_object"}``. Forwarded verbatim. Endpoints that
        don't support it usually ignore the field; Ollama specifically
        wants ``format: "json"`` instead, so we shim that case below.
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
        # P4-S25: structured output. OpenAI / chinzy / DashScope use
        # `response_format`; Ollama ignores that and reads `format`.
        # We pass the OpenAI-shape through and ALSO emit Ollama's
        # `format` so users can switch endpoints without code changes.
        if response_format is not None:
            payload["response_format"] = response_format
            rf_type = response_format.get("type") if isinstance(response_format, dict) else None
            if rf_type in ("json_object", "json_schema"):
                payload["format"] = "json"
        # P4-S25 A4: prompt caching — Anthropic-style cache_control
        # marker only fires for Anthropic-native endpoints. chinzy and
        # some sealos proxies treat unknown message fields strictly
        # and respond with empty content; OpenAI gpt-4o auto-caches
        # without the marker. So gate on URL keyword.
        if (
            messages
            and any(m.get("role") == "system" for m in messages)
            and _is_anthropic_endpoint(self.base_url)
        ):
            payload["messages"] = _stamp_cache_control(messages)
        # P4-S22 fix: wrap httpx errors so AgentLoop's existing
        # `except LLMProviderError` catches them and emits a clean
        # ErrorEvent instead of the bare ConnectError bubbling all the
        # way up to the chat handler (which surfaces as "chat_v2 错误:
        # unknown" in the UI because httpx.ConnectError on Windows
        # often has empty `str(exc)` when the remote sends RST mid
        # keep-alive). chinzy specifically does this between turns.
        # P4-S24: thinking-mode round-trip requirement. Some endpoints
        # (DeepSeek V4 Pro / chinzy.com proxy / Qwen3 thinking) reject
        # with HTTP 400 if a prior assistant message in `messages` is
        # missing its `reasoning_content`. This happens for rows
        # persisted before the round-trip fix landed (or any row where
        # storage dropped the field). On detection, strip the offending
        # rows and retry once. If the retry succeeds we move on; if it
        # fails again we surface the original error so the user sees
        # something stable.
        # P4-S24 transient-retry: chinzy.com / sealos / various OpenAI-
        # compat proxies sometimes drop the connection mid-request
        # (httpx surfaces this as RemoteProtocolError "Server
        # disconnected without sending a response" — common right
        # before a thinking-mode model emits its reasoning, since the
        # proxy's idle timeout fires while the upstream is still
        # computing). Auto-retry twice with short backoff before
        # surfacing the error to the user — fixes ~90% of one-off
        # blips without making the UI feel stuck. 4xx are NOT retried
        # here (HTTPStatusError raised by raise_for_status doesn't get
        # caught in this loop) so reasoning_content 400s still flow to
        # the strip-and-retry handler below.
        import asyncio as _asyncio

        async def _send(_msgs: list[dict]) -> dict:
            transient = (
                httpx.RemoteProtocolError,
                httpx.ConnectError,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
                httpx.PoolTimeout,
            )
            last_exc: Exception | None = None
            # P4-S25: 3 attempts with growing backoff. RemoteProtocolError
            # / connection drops on chinzy come in clusters — first 0.5s
            # backoff was too short. Failed attempts after the first
            # typically RST quickly so the worst-case wait stays bounded
            # (60s for the slow first attempt + a few seconds for the
            # quick fails on retries 2/3, NOT 3×60).
            backoffs = (0.0, 1.0, 3.0)
            for attempt, delay in enumerate(backoffs):
                if delay:
                    await _asyncio.sleep(delay)
                try:
                    async with self._client(timeout=self.timeout) as _client:
                        _r = await _client.post(
                            f"{self.base_url}/chat/completions",
                            json={**payload, "messages": _msgs},
                        )
                        _r.raise_for_status()
                        return _r.json()
                except httpx.HTTPStatusError:
                    # 4xx/5xx body present — propagate, retry happens at
                    # a different layer (reasoning_content 400 strip).
                    raise
                except transient as exc:
                    last_exc = exc
                    logger.warning(
                        "p4s24_transient_retry",
                        attempt=attempt + 1,
                        of=len(backoffs),
                        next_delay=backoffs[attempt + 1] if attempt + 1 < len(backoffs) else None,
                        error_type=type(exc).__name__,
                        error_msg=(str(exc) or type(exc).__name__)[:200],
                    )
                    continue
            # All retries exhausted.
            assert last_exc is not None
            raise last_exc

        try:
            data = await _send(messages)
        except httpx.HTTPStatusError as exc:
            body_snippet = ""
            try:
                body_snippet = exc.response.text[:600]
            except Exception:  # noqa: BLE001
                pass
            # 400 + the specific marker → retry once with assistant rows
            # missing reasoning_content stripped from history.
            should_retry = (
                exc.response.status_code == 400
                and "reasoning_content" in body_snippet
                and any(
                    m.get("role") == "assistant"
                    and not m.get("reasoning_content")
                    for m in messages
                )
            )
            if should_retry:
                stripped = [
                    m for m in messages
                    if not (
                        m.get("role") == "assistant"
                        and not m.get("reasoning_content")
                    )
                ]
                logger.warning(
                    "p4s24_reasoning_400_retry",
                    original_history_size=len(messages),
                    stripped_history_size=len(stripped),
                    dropped=len(messages) - len(stripped),
                )
                try:
                    data = await _send(stripped)
                except httpx.HTTPStatusError as exc2:
                    body2 = ""
                    try:
                        body2 = exc2.response.text[:300]
                    except Exception:  # noqa: BLE001
                        pass
                    raise LLMProviderError(
                        f"LLM HTTP {exc2.response.status_code} {exc2.response.reason_phrase}: "
                        f"{body2}"
                    ) from exc2
            else:
                raise LLMProviderError(
                    f"LLM HTTP {exc.response.status_code} {exc.response.reason_phrase}: "
                    f"{body_snippet[:300]}"
                ) from exc
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as exc:
            # Network-layer failure — typical when chinzy keep-alive
            # socket gets reset between turns. P5-S2 A2: classify by
            # phase so supervisor / UI can show actionable hints.
            _human_type = type(exc).__name__
            _human_msg = (str(exc) or _human_type).replace("\n", " ")[:300]
            _phase = (
                "pre-handshake" if isinstance(exc, httpx.ConnectError)
                else "read-timeout" if isinstance(exc, httpx.ReadTimeout)
                else "transient-other"
            )
            _advice = {
                "pre-handshake": (
                    f"无法连接到 LLM endpoint {self.base_url}（DNS/TCP/TLS 握手失败）。"
                    "请检查 base_url 是否拼写正确（如 .invalid 是占位符），"
                    "或上游服务是否在线。"
                ),
                "read-timeout": (
                    f"等待 {self.base_url} 返回数据超时。模型生成可能过慢或上游排队。"
                ),
                "transient-other": (
                    f"连接 {self.base_url} 时遇到 {_human_type}。"
                ),
            }.get(_phase, f"连接 {self.base_url} 失败：{_human_type}")
            raise LLMProviderError(
                f"LLM 调用失败 [{_phase}]: {_advice} 原始错误: {_human_msg}"
            ) from exc
        except httpx.HTTPError as exc:
            # Catch-all for any other transport / parsing httpx error.
            human = str(exc) or type(exc).__name__
            raise LLMProviderError(
                f"LLM 调用失败 ({human})。"
            ) from exc
        choices = data.get("choices") or []
        if not choices:
            return {
                "content": "",
                "reasoning_content": "",
                "tool_calls": [],
                "stop_reason": "error",
                "model": data.get("model", self.model),
                "usage": data.get("usage") or {},
            }
        c0 = choices[0]
        msg = c0.get("message") or {}
        finish = c0.get("finish_reason", "stop")
        # P4-S24: thinking-mode models (DeepSeek V4 Pro, Qwen3 thinking,
        # GLM-4.5, etc.) return a `reasoning_content` field alongside
        # `content`. Multi-turn chats MUST round-trip this field back to
        # the API or it rejects with HTTP 400 ("The reasoning_content
        # in the thinking mode must be passed back to the API"). Most
        # OpenAI-compat servers either omit the field or use the
        # canonical name; some non-conforming proxies use `reasoning`.
        reasoning_content = (
            msg.get("reasoning_content")
            or msg.get("reasoning")
            or ""
        )
        # Diagnostic: echo what we extracted + what's actually in the
        # outgoing request so multi-turn round-trip bugs are visible
        # in logs without needing a proxy / packet capture. INFO level
        # so it shows up in the default tauri-dev log.
        _has_assistant_history = any(
            m.get("role") == "assistant" for m in messages
        )
        logger.info(
            "p4s24_reasoning_extract",
            url=f"{self.base_url}/chat/completions",
            extracted_chars=len(reasoning_content),
            response_message_keys=list(msg.keys()),
            outgoing_history_size=len(messages),
            outgoing_has_assistant=_has_assistant_history,
            outgoing_assistant_has_reasoning=any(
                m.get("role") == "assistant" and m.get("reasoning_content")
                for m in messages
            ),
        )
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
            "reasoning_content": reasoning_content,
            "tool_calls": tcs,
            "stop_reason": stop_reason,
            "model": data.get("model", self.model),
            "usage": usage,
        }

    async def chat_stream_with_tools(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        max_tokens: int = 8192,
        temperature: float | None = None,
        response_format: dict | None = None,
    ):
        """P4-S25 A1: streaming version of chat_with_tools.

        Yields events as they arrive from the SSE stream:

            {"type": "delta", "content": str}           # text token chunk
            {"type": "delta_reasoning", "content": str} # thinking-mode chunk
            {"type": "final",                            # terminal event
             "content": str,
             "reasoning_content": str,
             "tool_calls": [...],
             "stop_reason": str,
             "model": str,
             "usage": {...}}

        The `final` event always fires once at the end (success or error
        — but on error we raise instead of yielding final). Tool calls
        are emitted only via `final.tool_calls` (assembled from streamed
        deltas) — not as their own event — so callers can treat them as
        a regular function-call response. Streaming JUST the content is
        the user-facing win: thinking-mode models like deepseek-v4-pro
        spend 30+ seconds in reasoning, and now the user sees the visible
        content trickle in as it generates instead of staring at a
        blank screen until the whole response lands.
        """
        import asyncio as _asyncio
        temp = temperature if temperature is not None else self.temperature
        self.last_usage = None
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": temp,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if response_format is not None:
            payload["response_format"] = response_format
            rf_type = response_format.get("type") if isinstance(response_format, dict) else None
            if rf_type in ("json_object", "json_schema"):
                payload["format"] = "json"
        if (
            messages
            and any(m.get("role") == "system" for m in messages)
            and _is_anthropic_endpoint(self.base_url)
        ):
            payload["messages"] = _stamp_cache_control(messages)

        # Streaming retry layer: same transient-error policy as the
        # non-streaming path. Bumped to 2 retries since SSE is more
        # exposed to mid-stream disconnects.
        transient = (
            httpx.RemoteProtocolError,
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
        )
        # P4-S25 streaming retry: chinzy / sealos proxies sometimes drop
        # the connection mid-stream (RemoteProtocolError "Server
        # disconnected without sending a response"). Two attempts
        # weren't enough — log captured a real run with both retries
        # failing 60s apart. Three attempts with longer backoff give
        # the proxy actual recovery time. RemoteProtocolError on a
        # closed socket fails fast, so worst-case isn't 60×3 — it's
        # 60s on the first attempt + a few seconds for each subsequent
        # quick-fail.
        backoffs = (0.0, 1.0, 3.0)
        last_exc: Exception | None = None
        # Used messages — may swap to a stripped version if we hit the
        # reasoning_content 400 mid-stream (rare, but old sessions still
        # have NULL reasoning_content rows).
        used_messages = messages
        used_payload = payload
        for attempt, delay in enumerate(backoffs):
            if delay:
                await _asyncio.sleep(delay)
            try:
                async for ev in self._stream_one_attempt(used_payload):
                    yield ev
                return
            except httpx.HTTPStatusError as exc:
                body = ""
                try:
                    body = exc.response.text[:600]
                except Exception:  # noqa: BLE001
                    pass
                # P4-S24 mirror in streaming: 400 reasoning_content →
                # strip stale assistant rows + retry once. Only retry
                # if we haven't already stripped (avoid infinite loop
                # if the retry also 400s for a different reason).
                stripped_already = used_messages is not messages
                if (
                    exc.response.status_code == 400
                    and "reasoning_content" in body
                    and not stripped_already
                    and any(
                        m.get("role") == "assistant"
                        and not m.get("reasoning_content")
                        for m in used_messages
                    )
                ):
                    used_messages = [
                        m for m in used_messages
                        if not (
                            m.get("role") == "assistant"
                            and not m.get("reasoning_content")
                        )
                    ]
                    used_payload = {**payload, "messages": used_messages}
                    if used_messages and any(m.get("role") == "system" for m in used_messages):
                        used_payload["messages"] = _stamp_cache_control(used_messages)
                    logger.warning(
                        "p4s25_stream_reasoning_400_retry",
                        original=len(messages),
                        stripped=len(used_messages),
                    )
                    continue  # retry the loop with stripped messages
                raise LLMProviderError(
                    f"LLM HTTP {exc.response.status_code} {exc.response.reason_phrase}: {body[:300]}"
                ) from exc
            except transient as exc:
                last_exc = exc
                # P5-S2 A2: classify transient errors so callers (supervisor,
                # UI, user) see a real cause instead of a bare "ConnectError".
                # Phase tags map to actionable user advice:
                #   pre-handshake   : DNS / TCP / TLS handshake fail → base_url
                #                     wrong, upstream completely down, or DNS
                #                     blocked (e.g. .invalid TLD).
                #   mid-stream-drop : SSE was started, proxy closed the socket
                #                     mid-flight → upstream proxy idle timeout
                #                     or load-balancer reset on long responses.
                #   read-timeout    : socket open but no bytes arrived in time
                #                     → model generation too slow / upstream
                #                     queueing.
                #   transient-other : write/pool timeouts, rare.
                _phase = (
                    "pre-handshake" if isinstance(exc, httpx.ConnectError)
                    else "mid-stream-drop" if isinstance(exc, httpx.RemoteProtocolError)
                    else "read-timeout" if isinstance(exc, httpx.ReadTimeout)
                    else "transient-other"
                )
                _msg = (str(exc) or type(exc).__name__).replace("\n", " ")[:300]
                logger.warning(
                    "p4s25_stream_transient_retry",
                    attempt=attempt + 1,
                    of=len(backoffs),
                    error_type=type(exc).__name__,
                    phase=_phase,
                    error_msg=_msg,
                    base_url=self.base_url,
                )
                continue
        assert last_exc is not None
        # P5-S2 A2: include actionable diagnosis in the user-facing error.
        # The base "LLM 调用失败 (ConnectError)" was opaque — users couldn't
        # tell apart "base_url wrong" from "upstream proxy dropped me".
        _human_type = type(last_exc).__name__
        _human_msg = (str(last_exc) or _human_type).replace("\n", " ")[:300]
        _phase = (
            "pre-handshake" if isinstance(last_exc, httpx.ConnectError)
            else "mid-stream-drop" if isinstance(last_exc, httpx.RemoteProtocolError)
            else "read-timeout" if isinstance(last_exc, httpx.ReadTimeout)
            else "transient-other"
        )
        # P5-S2 D1: stream → non-stream same-provider fallback.
        # Chinzy-like proxies idle-timeout long SSE streams (>~30s) and reset
        # the connection. A single POST that returns the whole completion
        # in one body completes inside the idle window, so falling back to
        # non-streaming after stream retries are exhausted typically
        # recovers without ever touching another provider.
        # Skip fallback only for `pre-handshake` (base_url wrong → non-stream
        # would fail identically) and HTTP-4xx (already raised above).
        if _phase in ("mid-stream-drop", "read-timeout", "transient-other"):
            try:
                logger.warning(
                    "p5s2_stream_fallback_to_nonstream phase=%s base_url=%s",
                    _phase, self.base_url,
                )
                # Use the REAL non-stream path (_legacy_chat_with_tools_nonstream).
                # chat_with_tools is actually stream-as-transport internally
                # (see its docstring), so it would re-enter the same broken
                # SSE path. _legacy_chat_with_tools_nonstream issues a single
                # `stream: false` POST + JSON parse — which completes inside
                # the chinzy proxy idle window and recovers cleanly.
                fallback_result = await self._legacy_chat_with_tools_nonstream(
                    messages,
                    tools=tools,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    response_format=response_format,
                )
                # Yield a synthetic terminal event so the streaming consumer
                # can treat this exactly like a completed SSE stream. No
                # delta events — the UI flips straight from "streaming" to
                # "done" but the final content is correct, which is better
                # than the alternative ("provider unavailable").
                yield {
                    "type": "final",
                    "content": fallback_result.get("content", ""),
                    "reasoning_content": fallback_result.get("reasoning_content", ""),
                    "tool_calls": fallback_result.get("tool_calls", []),
                    "stop_reason": fallback_result.get("stop_reason", "end_turn"),
                    "model": fallback_result.get("model", self.model),
                    "usage": fallback_result.get("usage", {}),
                    "fallback_used": "non_stream",
                }
                return
            except Exception as _fb_exc:  # noqa: BLE001
                logger.warning(
                    "p5s2_stream_fallback_to_nonstream_failed err=%s",
                    str(_fb_exc)[:200],
                )
                # Continue to raise the original stream error with diagnosis.
        _advice = {
            "pre-handshake": (
                f"无法连接到 LLM endpoint {self.base_url}（DNS/TCP/TLS 握手失败）。"
                "可能原因：base_url 拼写错（如 .invalid 占位符）、上游服务 down、"
                "或本机网络/防火墙阻塞。请在 Settings → LLM Providers 检查 base_url 并"
                "可考虑添加备用 provider。"
            ),
            "mid-stream-drop": (
                f"流式响应被 {self.base_url} 中途切断（连续 {len(backoffs)} 次）。"
                "通常是中转站负载均衡 idle-timeout 或长响应触发 proxy reset。"
                "降低 max_tokens、缩短 conversation history、或添加备用 provider 可缓解。"
            ),
            "read-timeout": (
                f"等待 {self.base_url} 返回数据超时（连续 {len(backoffs)} 次）。"
                "模型生成可能过慢或上游排队。可降级到更快的模型或增加 timeout。"
            ),
            "transient-other": (
                f"连接 {self.base_url} 时遇到 {_human_type}。"
            ),
        }.get(_phase, f"连接 {self.base_url} 失败：{_human_type}")
        raise LLMProviderError(
            f"LLM 调用失败 [{_phase}]: {_advice} 原始错误: {_human_msg}"
        ) from last_exc

    async def _stream_one_attempt(self, payload: dict):
        """Single-attempt SSE consumer. Used by chat_stream_with_tools."""
        import json as _json
        # Accumulators for assembling the final event.
        full_content = ""
        full_reasoning = ""
        # Partial tool_calls indexed by `index` field. Each entry holds
        # {id, name, args_buf}; on stream end we json.loads(args_buf).
        tool_buffers: dict[int, dict] = {}
        final_stop = "end_turn"
        final_model = self.model
        final_usage: dict = {}
        # P4-S25 diagnostic
        sse_lines = 0
        delta_keys_seen: set[str] = set()

        async with self._client(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
            ) as response:
                response.raise_for_status()
                # P4-S25 fix: chinzy / sealos / DashScope sometimes ignore
                # `stream: True` for thinking-mode models and return a
                # plain JSON body (Content-Type: application/json) holding
                # the complete response. aiter_lines() yields ONE line of
                # raw JSON which doesn't start with "data:" — we'd skip
                # it and end up with empty final content. Detect that
                # case via content-type and parse the body as a single
                # non-streaming response, then synthesize a final event
                # without yielding any deltas (we have no incremental
                # data anyway).
                content_type = (response.headers.get("content-type") or "").lower()
                if "text/event-stream" not in content_type:
                    body_bytes = await response.aread()
                    body_text = body_bytes.decode("utf-8", errors="replace")
                    try:
                        data = _json.loads(body_text)
                    except _json.JSONDecodeError:
                        logger.warning(
                            "p4s25_stream_unexpected_body",
                            content_type=content_type,
                            body_preview=body_text[:300],
                        )
                        # Fall through to the SSE-line loop with an
                        # empty body — will emit an empty final.
                        data = None
                    if isinstance(data, dict):
                        choices = data.get("choices") or []
                        c0 = choices[0] if choices else {}
                        msg = c0.get("message") or {}
                        full_content = msg.get("content") or ""
                        full_reasoning = (
                            msg.get("reasoning_content")
                            or msg.get("reasoning")
                            or ""
                        )
                        for j, tc in enumerate(msg.get("tool_calls") or []):
                            fn = tc.get("function") or {}
                            tool_buffers[j] = {
                                "id": tc.get("id", "") or "",
                                "name": fn.get("name", "") or "",
                                "args_buf": fn.get("arguments", "") or "",
                            }
                        finish_reason = c0.get("finish_reason") or "stop"
                        final_stop = (
                            "tool_use" if finish_reason == "tool_calls"
                            else "end_turn" if finish_reason == "stop"
                            else finish_reason
                        )
                        final_model = data.get("model") or final_model
                        usage = data.get("usage") or {}
                        if usage:
                            final_usage = usage
                            self.last_usage = usage
                        # Emit one synthetic delta so callers that depend
                        # on `delta_count > 0` (agent_loop's empty-stream
                        # fallback) treat this as a real response.
                        if full_content:
                            yield {"type": "delta", "content": full_content}
                        if full_reasoning:
                            yield {"type": "delta_reasoning", "content": full_reasoning}
                        logger.info(
                            "p4s25_nonstream_body_via_stream_endpoint",
                            content_type=content_type,
                            content_chars=len(full_content),
                            reasoning_chars=len(full_reasoning),
                            tool_calls=len(tool_buffers),
                        )
                        # Skip the SSE-line loop entirely — we already
                        # have everything.
                        sse_lines = -1  # marker for "non-stream body"
                # P4-S25 fix-2: capture every raw line so we can fall
                # back to "treat the whole body as JSON" if the SSE loop
                # produces nothing useful. Some chinzy responses come
                # back with Content-Type: text/event-stream but the body
                # is a single JSON object (no `data:` prefix) — the
                # content-type check above won't catch that.
                # Capture EVERY line including empty ones so we can
                # accurately diagnose what chinzy actually sent.
                raw_lines: list[str] = []
                async for line in response.aiter_lines():
                    if sse_lines == -1:
                        # Non-stream body branch already consumed
                        # response.aread(); aiter_lines is empty here
                        # but defend against the iterator yielding
                        # anything anyway.
                        break
                    sse_lines += 1
                    raw_lines.append(line)  # capture even empty lines
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[len("data:"):].strip()
                    if not data_str or data_str == "[DONE]":
                        if data_str == "[DONE]":
                            break
                        continue
                    try:
                        data = _json.loads(data_str)
                    except _json.JSONDecodeError:
                        continue
                    final_model = data.get("model") or final_model
                    usage = data.get("usage")
                    if usage:
                        final_usage = usage
                        self.last_usage = usage
                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    delta_keys_seen.update(delta.keys())
                    # P4-S25 diagnostic: some non-conforming proxies put
                    # the full message in `choices[0].message` (only at
                    # the end) instead of streaming via `delta`. Capture
                    # that as a fallback so we don't end up with empty
                    # final_content. chinzy seems to do this for some
                    # thinking-mode models.
                    msg = choices[0].get("message") or {}
                    if msg.get("content") and not full_content:
                        full_content = msg["content"]
                    if msg.get("reasoning_content") and not full_reasoning:
                        full_reasoning = msg["reasoning_content"]
                    if msg.get("tool_calls") and not tool_buffers:
                        for j, tc in enumerate(msg.get("tool_calls") or []):
                            fn = tc.get("function") or {}
                            tool_buffers[j] = {
                                "id": tc.get("id", "") or "",
                                "name": fn.get("name", "") or "",
                                "args_buf": fn.get("arguments", "") or "",
                            }
                    finish_reason = choices[0].get("finish_reason")
                    # Visible content delta.
                    txt = delta.get("content")
                    if txt:
                        full_content += txt
                        yield {"type": "delta", "content": txt}
                    # Thinking-mode reasoning delta (DeepSeek V4 Pro etc.).
                    rtxt = delta.get("reasoning_content") or delta.get("reasoning")
                    if rtxt:
                        full_reasoning += rtxt
                        yield {"type": "delta_reasoning", "content": rtxt}
                    # Tool_call deltas: name on first chunk, args dripped
                    # across many. Buffer per-index until stream ends.
                    for tcd in delta.get("tool_calls") or []:
                        idx = tcd.get("index", 0) or 0
                        buf = tool_buffers.setdefault(
                            idx, {"id": "", "name": "", "args_buf": ""}
                        )
                        if tcd.get("id"):
                            buf["id"] = tcd["id"]
                        fn = tcd.get("function") or {}
                        if fn.get("name"):
                            buf["name"] = fn["name"]
                        if fn.get("arguments"):
                            buf["args_buf"] += fn["arguments"]
                    if finish_reason:
                        final_stop = (
                            "tool_use" if finish_reason == "tool_calls"
                            else "end_turn" if finish_reason == "stop"
                            else finish_reason
                        )

        # P4-S25 fix-2: post-loop manual SSE parser.
        #
        # Root cause discovered 2026-05-09: httpx.aiter_lines() against
        # chinzy.com sometimes returns the ENTIRE SSE body as a single
        # line — multiple `data: {chunk}\n\ndata: {chunk}\n\n...` events
        # smushed together without splitting on the inner newlines. So
        # the streaming-line loop above sees just one line, fails the
        # `data:` prefix check on the whole concat, and yields nothing.
        # When that happens, we fall back here: take the joined raw
        # body and manually split it into SSE events on `\n\n` block
        # boundaries (the SSE spec separator), then parse each event.
        empty_response = (
            sse_lines != -1
            and not full_content
            and not full_reasoning
            and not tool_buffers
        )
        if empty_response:
            joined = "\n".join(raw_lines)
            # Always log raw evidence on empty-response so we can keep
            # diagnosing if this branch ALSO breaks.
            logger.warning(
                "p4s25_stream_empty_response_raw_dump",
                sse_lines=sse_lines,
                raw_lines_count=len(raw_lines),
                body_preview=joined[:400],
            )
            # 2026-05-09 chinzy quirk: their thinking-mode endpoint
            # returns the ENTIRE SSE body as a single JSON-encoded
            # string — i.e. the bytes on the wire look like
            #     "data: {...}\n\ndata: {...}\n\n..."
            # (note the leading + trailing `"`, and `\n` is a 2-byte
            # backslash-n escape, NOT a real newline byte). aiter_lines
            # therefore yields ONE line and our split-on-newline parser
            # finds zero events. Detect and unwrap.
            if (
                joined.startswith('"')
                and joined.endswith('"')
                and "\\n" in joined
            ):
                try:
                    joined = _json.loads(joined)
                    logger.info(
                        "p4s25_stream_unwrapped_json_string_body",
                        unwrapped_chars=len(joined),
                    )
                except _json.JSONDecodeError:
                    pass  # leave joined as-is; manual split may still work
            # SSE event delimiter is a blank line.
            events = [ev.strip() for ev in joined.split("\n\n") if ev.strip()]
            recovered_chunks = 0
            for event in events:
                # Each event may have multiple lines; collect data: ones.
                for ev_line in event.splitlines():
                    if not ev_line.startswith("data:"):
                        continue
                    payload_str = ev_line[len("data:"):].strip()
                    if not payload_str or payload_str == "[DONE]":
                        continue
                    try:
                        data = _json.loads(payload_str)
                    except _json.JSONDecodeError:
                        continue
                    final_model = data.get("model") or final_model
                    usage = data.get("usage")
                    if usage:
                        final_usage = usage
                        self.last_usage = usage
                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    c0 = choices[0]
                    delta = c0.get("delta") or {}
                    msg = c0.get("message") or {}
                    txt = delta.get("content") or msg.get("content")
                    if txt:
                        full_content += txt
                        recovered_chunks += 1
                    rtxt = (
                        delta.get("reasoning_content")
                        or delta.get("reasoning")
                        or msg.get("reasoning_content")
                        or msg.get("reasoning")
                    )
                    if rtxt:
                        full_reasoning += rtxt
                        recovered_chunks += 1
                    for tcd in delta.get("tool_calls") or msg.get("tool_calls") or []:
                        idx = tcd.get("index", 0) or 0
                        buf = tool_buffers.setdefault(
                            idx, {"id": "", "name": "", "args_buf": ""}
                        )
                        if tcd.get("id"):
                            buf["id"] = tcd["id"]
                        fn = tcd.get("function") or {}
                        if fn.get("name"):
                            buf["name"] = fn["name"]
                        if fn.get("arguments"):
                            buf["args_buf"] += fn["arguments"]
                    fr = c0.get("finish_reason")
                    if fr:
                        final_stop = (
                            "tool_use" if fr == "tool_calls"
                            else "end_turn" if fr == "stop"
                            else fr
                        )
            if recovered_chunks or tool_buffers:
                # Emit a single delta so callers waiting for stream
                # output (delta_count) treat this as a real response.
                if full_content:
                    yield {"type": "delta", "content": full_content}
                if full_reasoning:
                    yield {"type": "delta_reasoning", "content": full_reasoning}
                logger.info(
                    "p4s25_stream_postloop_sse_recovered",
                    events=len(events),
                    chunks=recovered_chunks,
                    content_chars=len(full_content),
                    reasoning_chars=len(full_reasoning),
                    tool_calls=len(tool_buffers),
                )
            else:
                logger.warning(
                    "p4s25_stream_empty_body_unparseable",
                    sse_lines=sse_lines,
                    events_count=len(events),
                    body_preview=joined[:300],
                )

        # P5-S2 Phase 1 — diagnostic dump for every accumulated tool_call
        # buffer. chinzy / sealos sometimes truncate the SSE mid-frame
        # leaving us with half-baked args (e.g. `{"path": "fo`); without
        # this log we can't tell at debug-time whether the model emitted
        # broken JSON, the proxy chopped the stream, or our parser ate
        # fragments. Emit BEFORE assembly so the raw buf is unambiguous.
        # Pure observation — no behaviour change.
        for _idx in sorted(tool_buffers.keys()):
            _buf = tool_buffers[_idx]
            _args_str = _buf.get("args_buf", "") or ""
            try:
                # Empty buf {} is a legal "no-arg call" — treat as parseable.
                _json.loads(_args_str) if _args_str else _json.loads("{}")
                _parse_ok = True
            except _json.JSONDecodeError:
                _parse_ok = False
            logger.info(
                "p5s2_tool_call_args_dump",
                idx=_idx,
                name=_buf.get("name", "") or "",
                args_len=len(_args_str),
                args_preview=_args_str[:100],
                parse_ok=_parse_ok,
            )

        # Assemble tool_calls from buffers.
        #
        # P5-S2 (2026-05-11) fix: when the model emits malformed JSON for
        # tool_call.arguments (deepseek-v4-pro on long markdown content
        # frequently mis-escapes \n / " / \\), we used to silently swallow
        # the JSONDecodeError and pass `args = {}` to the dispatcher. The
        # tool then reports "missing required parameter" → LLM retries
        # with the same broken JSON → 3 strikes → circuit breaker OPEN →
        # user gets popup. The model never learns what went wrong.
        #
        # Now: stash the raw args_buf + parse error on the assembled
        # tool_call dict (keys ``_args_raw`` + ``_args_parse_error``).
        # Downstream (``_raw_to_response`` → ChatResponse → AgentLoop)
        # detects these and short-circuits dispatch with a structured
        # tool_result that tells the LLM exactly what's wrong, so it can
        # regenerate the call with valid JSON.
        assembled_tools: list[dict] = []
        for idx in sorted(tool_buffers.keys()):
            buf = tool_buffers[idx]
            args_buf = buf["args_buf"] or ""
            args: dict = {}
            args_parse_error: str | None = None
            if args_buf:
                try:
                    parsed = _json.loads(args_buf)
                    args = parsed if isinstance(parsed, dict) else {}
                except _json.JSONDecodeError as _exc:
                    # P6 bugfix 2026-05-14 (live-test): thinking-mode LLMs
                    # writing React/JS code commonly escape single quotes
                    # as ``\'`` — invalid JSON (single quotes don't need
                    # escaping). The whole 6KB write_file tool_call then
                    # fails parse and the agent gets stuck retrying the
                    # same broken JSON until circuit-breaker opens. Try
                    # cheap repairs before giving up: any successful
                    # repair short-circuits to a valid args dict.
                    args = {}
                    args_parse_error = str(_exc)
                    repaired: dict | None = None
                    repair_label: str | None = None
                    # Common LLM escape mistakes (safe to apply because
                    # valid JSON never contains these sequences). Try
                    # repairs in escalating aggressiveness; first success
                    # short-circuits.
                    #
                    # Repair 1 (cheap, common): ``\'`` → ``'`` —
                    #   thinking-mode LLMs writing React/JS code routinely
                    #   pseudo-escape apostrophes.
                    repair_candidates: list[tuple[str, str]] = []
                    if "\\'" in args_buf:
                        repair_candidates.append((
                            "stripped_backslash_apostrophe",
                            args_buf.replace("\\'", "'"),
                        ))
                    # Repair 2 (broad, regex): drop any invalid ``\X``
                    #   escape (X not in legal JSON escape set
                    #   ``"\/ bfnrtu``). This catches \', \;, \ , \., etc.
                    #   Crucially, leaves legal escapes (\\, \", \n, \t,
                    #   \uXXXX, …) intact because the regex's negated
                    #   class excludes the legal followers.
                    import re as _re
                    _invalid_esc = _re.compile(r'\\([^"\\/bfnrtu])')
                    if _invalid_esc.search(args_buf):
                        repair_candidates.append((
                            "stripped_invalid_escapes",
                            _invalid_esc.sub(r"\1", args_buf),
                        ))
                    # Repair 3 (last resort): permissive parse with
                    #   strict=False — accepts unescaped control chars
                    #   (tabs, newlines) inside strings. Doesn't fix
                    #   bad escapes but does handle raw \n in content.
                    repair_candidates.append((
                        "strict_false",
                        args_buf,  # decoder will use strict=False
                    ))
                    for _label, _cand in repair_candidates:
                        try:
                            if _label == "strict_false":
                                _r = _json.JSONDecoder(strict=False).decode(_cand)
                            else:
                                _r = _json.loads(_cand)
                            if isinstance(_r, dict):
                                repaired = _r
                                repair_label = _label
                                break
                        except _json.JSONDecodeError:
                            continue
                    if repaired is not None:
                        args = repaired
                        args_parse_error = None
                        logger.info(
                            "p5s2_tool_call_args_repaired",
                            idx=idx,
                            name=buf.get("name", "") or "",
                            args_len=len(args_buf),
                            repair=repair_label,
                            orig_parse_error=str(_exc),
                        )
                    else:
                        # Dump the FULL args_buf (capped at 5KB to keep log
                        # readable) when repair also fails. This is the only
                        # way to diagnose new model-side JSON-escape bugs
                        # vs proxy-side truncation.
                        logger.warning(
                            "p5s2_tool_call_args_malformed",
                            idx=idx,
                            name=buf.get("name", "") or "",
                            args_len=len(args_buf),
                            args_full=args_buf[:5000],
                            parse_error=args_parse_error,
                        )
            tc_dict = {
                "id": buf["id"] or f"call_{idx}",
                "name": buf["name"],
                "arguments": args,
            }
            if args_parse_error:
                tc_dict["_args_raw"] = args_buf
                tc_dict["_args_parse_error"] = args_parse_error
            assembled_tools.append(tc_dict)
        # If there were tool_calls, override stop_reason — some servers
        # don't set finish_reason="tool_calls" on the streaming end frame.
        if assembled_tools and final_stop != "tool_use":
            final_stop = "tool_use"

        logger.info(
            "p4s25_stream_summary",
            sse_lines=sse_lines,
            content_chars=len(full_content),
            reasoning_chars=len(full_reasoning),
            tool_calls=len(assembled_tools),
            delta_keys=sorted(delta_keys_seen),
            stop_reason=final_stop,
        )

        yield {
            "type": "final",
            "content": full_content,
            "reasoning_content": full_reasoning,
            "tool_calls": assembled_tools,
            "stop_reason": final_stop,
            "model": final_model,
            "usage": final_usage,
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


def _is_anthropic_endpoint(base_url: str) -> bool:
    """Heuristic: should we emit Anthropic-style cache_control marks?

    True only for endpoints that we have evidence handle the field.
    Most OpenAI-compat proxies (chinzy, sealos, vllm, ollama) reject
    unknown message keys silently — observed empirically when chinzy
    started returning empty content after we started emitting
    cache_control. Better to no-op than break working flows.
    """
    u = (base_url or "").lower()
    return "anthropic.com" in u or "claude" in u


def _stamp_cache_control(messages: list[dict]) -> list[dict]:
    """Return a copy of `messages` with cache_control on the last system msg.

    P4-S25: Anthropic prompt caching uses ``cache_control: {type:"ephemeral"}``
    on the message whose prefix should be cached. The frozen system stack
    (persona + skill_prelude + memory_block) lives at the front of every
    request; marking the last system message tells Anthropic "everything
    up to here is cacheable". OpenAI ignores the field; gpt-4o auto-caches
    based on prefix. Ollama / chinzy strip unknown fields. Safe to always
    emit.
    """
    last_sys_idx = -1
    for i, m in enumerate(messages):
        if m.get("role") == "system":
            last_sys_idx = i
    if last_sys_idx < 0:
        return messages
    out: list[dict] = []
    for i, m in enumerate(messages):
        if i == last_sys_idx and isinstance(m.get("content"), str):
            stamped = dict(m)
            stamped["cache_control"] = {"type": "ephemeral"}
            out.append(stamped)
        else:
            out.append(m)
    return out
