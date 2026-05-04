"""P4-S20 Wave 2b: OpenAICompatibleProvider.chat_with_tools + shim tests."""
from __future__ import annotations

import json

import httpx
import pytest

from providers.openai_compatible import OpenAICompatibleProvider
from agent.tool_use_shim import OpenAICompatibleAgentLLM


def _mock_handler(captured: list, response_body: dict):
    def _h(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=response_body)

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
