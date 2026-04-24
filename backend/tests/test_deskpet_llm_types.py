"""Unit tests for llm.types dataclasses (Pydantic v2 round-trip)."""
from __future__ import annotations

from llm.types import ChatChunk, ChatResponse, ChatUsage, ToolCall


def test_chat_usage_defaults_zero():
    u = ChatUsage()
    assert u.input_tokens == 0
    assert u.output_tokens == 0
    assert u.cache_read_tokens == 0
    assert u.cache_write_tokens == 0


def test_tool_call_round_trip():
    tc = ToolCall(id="call_1", name="web_fetch", arguments={"url": "https://example.com"})
    data = tc.model_dump()
    assert data == {
        "id": "call_1",
        "name": "web_fetch",
        "arguments": {"url": "https://example.com"},
    }
    restored = ToolCall.model_validate(data)
    assert restored == tc


def test_chat_response_round_trip():
    resp = ChatResponse(
        content="Hello!",
        tool_calls=[ToolCall(id="t1", name="get_time", arguments={})],
        stop_reason="tool_use",
        usage=ChatUsage(
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=800,
            cache_write_tokens=10,
        ),
        model="claude-sonnet-4-5",
    )
    data = resp.model_dump()
    restored = ChatResponse.model_validate(data)
    assert restored == resp
    # All 4 usage fields MUST be present even after round-trip (spec §llm-providers).
    assert set(data["usage"].keys()) >= {
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
    }


def test_chat_response_defaults_safe():
    r = ChatResponse()
    assert r.content == ""
    assert r.tool_calls == []
    assert r.stop_reason == ""
    assert r.usage == ChatUsage()


def test_chat_chunk_final_marker():
    c = ChatChunk(is_final=True, stop_reason="end_turn", usage=ChatUsage(input_tokens=10))
    assert c.is_final is True
    assert c.usage and c.usage.input_tokens == 10


def test_chat_chunk_delta_only():
    c = ChatChunk(delta_content="hello ", model="gpt-4o")
    assert c.delta_content == "hello "
    assert c.is_final is False
    assert c.usage is None
