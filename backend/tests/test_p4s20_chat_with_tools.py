"""P4-S20 Wave 2b: OpenAICompatibleProvider.chat_with_tools + shim tests."""
from __future__ import annotations

import json

import httpx
import pytest

from providers.openai_compatible import OpenAICompatibleProvider
from agent.tool_use_shim import OpenAICompatibleAgentLLM


def _mock_handler(captured: list, response_body: dict):
    """SSE-shape mock handler.

    P4-S25 fix: ``chat_with_tools`` now uses ``stream: True`` under the
    hood (delegates to ``chat_stream_with_tools``). The mock therefore
    serves the OpenAI choice shape as a single SSE event followed by
    ``[DONE]``. The streaming consumer's ``choices[0].message`` fallback
    branch picks up content / tool_calls from this shape, exactly as
    chinzy and other non-conforming proxies emit them.
    """
    def _h(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        sse = f"data: {json.dumps(response_body)}\n\ndata: [DONE]\n\n"
        return httpx.Response(
            200,
            content=sse.encode("utf-8"),
            headers={"content-type": "text/event-stream"},
        )

    return _h


@pytest.mark.asyncio
async def test_chat_with_tools_parses_tool_calls() -> None:
    """OpenAI-shape response with tool_calls → parsed into our shape."""
    captured: list[dict] = []
    body = {
        "id": "x",
        "model": "gpt-test",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "desktop_create_file",
                                "arguments": json.dumps(
                                    {"name": "todo.txt", "content": "hi"}
                                ),
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 30, "completion_tokens": 10},
    }
    p = OpenAICompatibleProvider(
        base_url="https://stub", api_key="k", model="gpt-test"
    )
    p._test_transport = httpx.MockTransport(
        _mock_handler(captured, body)
    )

    out = await p.chat_with_tools(
        [{"role": "user", "content": "create todo"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "desktop_create_file",
                    "description": "create",
                    "parameters": {"type": "object"},
                },
            }
        ],
    )
    assert out["stop_reason"] == "tool_use"
    assert len(out["tool_calls"]) == 1
    tc = out["tool_calls"][0]
    assert tc["name"] == "desktop_create_file"
    assert tc["arguments"] == {"name": "todo.txt", "content": "hi"}
    assert captured[0]["tools"][0]["function"]["name"] == "desktop_create_file"


@pytest.mark.asyncio
async def test_chat_with_tools_normal_text() -> None:
    body = {
        "id": "x",
        "model": "gpt-test",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "hello"},
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5},
    }
    captured: list[dict] = []
    p = OpenAICompatibleProvider(
        base_url="https://stub", api_key="k", model="gpt-test"
    )
    p._test_transport = httpx.MockTransport(
        _mock_handler(captured, body)
    )
    out = await p.chat_with_tools([{"role": "user", "content": "hi"}])
    assert out["content"] == "hello"
    assert out["stop_reason"] == "end_turn"
    assert out["tool_calls"] == []


@pytest.mark.asyncio
async def test_shim_returns_chatresponse() -> None:
    body = {
        "id": "x",
        "model": "gpt-test",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "tool_calls": [
                        {
                            "id": "c1",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path":"a.txt"}',
                            },
                        }
                    ],
                    "content": None,
                },
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 0},
    }
    p = OpenAICompatibleProvider(
        base_url="https://stub", api_key="k", model="gpt-test"
    )
    p._test_transport = httpx.MockTransport(_mock_handler([], body))
    shim = OpenAICompatibleAgentLLM(provider=p)
    resp = await shim.chat_with_fallback(
        [{"role": "user", "content": "x"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "r",
                    "parameters": {},
                },
            }
        ],
    )
    assert resp.stop_reason == "tool_use"
    assert resp.tool_calls[0].name == "read_file"
    assert resp.tool_calls[0].arguments == {"path": "a.txt"}
    assert resp.usage.input_tokens == 10


# Regression: chinzy.com / sealos thinking-mode endpoints sometimes
# wrap the entire SSE response body as a single JSON-encoded string.
# i.e. the bytes on the wire look like
#     "data: {chunk1}\n\ndata: {chunk2}\n\n..."
# (literal leading + trailing `"`, `\n` is a 2-byte backslash-n escape,
# NOT a real newline byte). The standard SSE parser sees one giant line
# that doesn't start with `data:` and yields nothing. The post-loop
# unwrap path detects this shape, json.loads-unwraps it, and then
# manually re-parses the inner SSE.
@pytest.mark.asyncio
async def test_chinzy_double_encoded_sse_recovers() -> None:
    """A response body that's a JSON-string-wrapped SSE stream
    must still produce a usable content + reasoning_content."""
    inner_chunks = [
        {
            "id": "x",
            "object": "chat.completion.chunk",
            "model": "deepseek-v4-pro",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "reasoning_content": "thinking..."},
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "x",
            "object": "chat.completion.chunk",
            "model": "deepseek-v4-pro",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "hello"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 1},
        },
    ]
    sse_inner = "".join(f"data: {json.dumps(c)}\n\n" for c in inner_chunks) + "data: [DONE]\n\n"
    # CHINZY QUIRK: wrap the whole SSE in one JSON-encoded string.
    wrapped = json.dumps(sse_inner)

    def _h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=wrapped.encode("utf-8"),
            headers={"content-type": "text/event-stream"},
        )

    p = OpenAICompatibleProvider(
        base_url="https://chinzy.example", api_key="k", model="deepseek-v4-pro"
    )
    p._test_transport = httpx.MockTransport(_h)
    out = await p.chat_with_tools([{"role": "user", "content": "ping"}])
    assert out["content"] == "hello", f"content lost: {out!r}"
    assert "thinking" in (out.get("reasoning_content") or ""), (
        f"reasoning lost: {out!r}"
    )
    assert out["stop_reason"] == "end_turn"
