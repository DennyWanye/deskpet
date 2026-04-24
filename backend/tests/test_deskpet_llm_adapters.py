"""Unit tests for adapter conversion/normalization logic (no real API calls).

Uses monkeypatched SDK clients so:
    - anthropic: verify prompt-caching cache_control placement
    - openai:    verify tool_call JSON parsing + malformed arg rejection
    - gemini:    verify OpenAI-schema → FunctionDeclaration conversion
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from llm.anthropic_adapter import AnthropicAdapter
from llm.errors import LLMProviderError
from llm.gemini_adapter import GeminiAdapter
from llm.openai_adapter import OpenAIAdapter


# ───────────────────── Anthropic ─────────────────────


def test_anthropic_system_split_adds_cache_control():
    """Last system block MUST have cache_control={'type':'ephemeral'}."""
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "system", "content": "Frozen policy."},
        {"role": "user", "content": "Hi"},
    ]
    system, chat = AnthropicAdapter._split_system_messages(messages)
    assert len(system) == 2
    assert "cache_control" not in system[0]
    assert system[-1]["cache_control"] == {"type": "ephemeral"}
    assert system[-1]["text"] == "Frozen policy."
    assert len(chat) == 1 and chat[0]["role"] == "user"


def test_anthropic_tool_conversion_marks_last_tool_cacheable():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_time",
                "description": "returns time",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_fetch",
                "description": "fetch url",
                "parameters": {"type": "object", "properties": {"url": {"type": "string"}}},
            },
        },
    ]
    converted = AnthropicAdapter._convert_tools(tools)
    assert converted is not None
    assert len(converted) == 2
    assert "cache_control" not in converted[0]
    assert converted[-1]["cache_control"] == {"type": "ephemeral"}
    # Schema field name differs between APIs.
    assert "input_schema" in converted[0]
    assert converted[0]["name"] == "get_time"


def test_anthropic_build_response_maps_tool_use_block():
    adapter = AnthropicAdapter(api_key="sk-testkey-abcd")
    block_text = SimpleNamespace(type="text", text="Let me check.")
    block_tool = SimpleNamespace(
        type="tool_use", id="tu_1", name="get_time", input={"tz": "UTC"}
    )
    mock_resp = SimpleNamespace(
        content=[block_text, block_tool],
        stop_reason="tool_use",
        model="claude-sonnet-4-5",
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=30,
            cache_read_input_tokens=900,
            cache_creation_input_tokens=20,
        ),
    )
    resp = adapter._build_response(mock_resp, "claude-sonnet-4-5")
    assert resp.content == "Let me check."
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].id == "tu_1"
    assert resp.tool_calls[0].name == "get_time"
    assert resp.tool_calls[0].arguments == {"tz": "UTC"}
    assert resp.stop_reason == "tool_use"
    assert resp.usage.input_tokens == 100
    assert resp.usage.output_tokens == 30
    assert resp.usage.cache_read_tokens == 900
    assert resp.usage.cache_write_tokens == 20


def test_anthropic_available_needs_key():
    ad = AnthropicAdapter(api_key=None)
    # When nothing in env & no keyring, should be False.
    import os

    if not os.environ.get("ANTHROPIC_API_KEY"):
        assert ad.available() is False


def test_anthropic_tool_message_becomes_user_tool_result():
    """OpenAI-style tool message should convert to anthropic tool_result block."""
    messages = [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "tu_1", "type": "function", "function": {"name": "get_time", "arguments": {}}}
        ]},
        {"role": "tool", "tool_call_id": "tu_1", "name": "get_time", "content": '{"time":"10:00"}'},
    ]
    system, chat = AnthropicAdapter._split_system_messages(messages)
    assert system == []
    # user, assistant(with tool_use), user(tool_result)
    assert chat[2]["role"] == "user"
    assert chat[2]["content"][0]["type"] == "tool_result"
    assert chat[2]["content"][0]["tool_use_id"] == "tu_1"


# ───────────────────── OpenAI ─────────────────────


def test_openai_parse_tool_args_accepts_dict_and_json_string():
    assert OpenAIAdapter._parse_tool_args({"x": 1}) == {"x": 1}
    assert OpenAIAdapter._parse_tool_args('{"y": 2}') == {"y": 2}
    assert OpenAIAdapter._parse_tool_args(None) == {}
    assert OpenAIAdapter._parse_tool_args("") == {}


def test_openai_parse_tool_args_rejects_malformed_json():
    with pytest.raises(LLMProviderError, match="not valid JSON"):
        OpenAIAdapter._parse_tool_args("not valid json")


def test_openai_parse_tool_args_rejects_non_object():
    with pytest.raises(LLMProviderError, match="non-object"):
        OpenAIAdapter._parse_tool_args("[1, 2, 3]")


def test_openai_usage_splits_cached_tokens_out_of_prompt():
    """prompt_tokens is inclusive of cached, but we want separate axes."""
    usage = SimpleNamespace(
        prompt_tokens=1000,
        completion_tokens=50,
        prompt_tokens_details=SimpleNamespace(cached_tokens=800),
    )
    cu = OpenAIAdapter._usage_from(usage)
    assert cu.input_tokens == 200  # 1000 - 800
    assert cu.cache_read_tokens == 800
    assert cu.output_tokens == 50
    assert cu.cache_write_tokens == 0


def test_openai_usage_handles_missing_details():
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20)
    cu = OpenAIAdapter._usage_from(usage)
    assert cu.input_tokens == 100
    assert cu.cache_read_tokens == 0


def test_openai_map_stop_reason():
    assert OpenAIAdapter._map_stop_reason("stop") == "end_turn"
    assert OpenAIAdapter._map_stop_reason("tool_calls") == "tool_use"
    assert OpenAIAdapter._map_stop_reason("length") == "max_tokens"
    assert OpenAIAdapter._map_stop_reason(None) == "end_turn"


# ───────────────────── Gemini ─────────────────────


def test_gemini_split_system_joins_multiple():
    messages = [
        {"role": "system", "content": "Rule A"},
        {"role": "system", "content": "Rule B"},
        {"role": "user", "content": "Hi"},
    ]
    sys_text, chat = GeminiAdapter._split_system(messages)
    assert sys_text == "Rule A\n\nRule B"
    assert len(chat) == 1 and chat[0]["role"] == "user"


def test_gemini_split_system_handles_list_content():
    messages = [
        {"role": "system", "content": [{"type": "text", "text": "block1"}, {"type": "text", "text": "block2"}]},
    ]
    sys_text, chat = GeminiAdapter._split_system(messages)
    assert sys_text == "block1\n\nblock2"


def test_gemini_convert_tools_shape():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_time",
                "description": "returns time",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    converted = GeminiAdapter._convert_tools(tools)
    assert converted is not None
    assert len(converted) == 1
    # The shape is a google-genai Tool object — hasattr function_declarations.
    tool = converted[0]
    assert hasattr(tool, "function_declarations")
    decls = tool.function_declarations
    assert decls is not None
    assert decls[0].name == "get_time"


def test_gemini_map_finish_reason():
    stop = SimpleNamespace(name="STOP")
    assert GeminiAdapter._map_finish_reason(stop) == "end_turn"
    mt = SimpleNamespace(name="MAX_TOKENS")
    assert GeminiAdapter._map_finish_reason(mt) == "max_tokens"
    assert GeminiAdapter._map_finish_reason(None) == "end_turn"


def test_gemini_fresh_call_id_format():
    cid = GeminiAdapter._fresh_call_id()
    assert cid.startswith("gemini_")
    assert len(cid) > len("gemini_") + 5


def test_gemini_usage_subtracts_cached():
    meta = SimpleNamespace(
        prompt_token_count=500,
        candidates_token_count=100,
        cached_content_token_count=300,
    )
    cu = GeminiAdapter._usage_from(meta)
    assert cu.input_tokens == 200
    assert cu.cache_read_tokens == 300
    assert cu.output_tokens == 100
    assert cu.cache_write_tokens == 0


def test_gemini_build_response_mints_tool_call_ids():
    adapter = GeminiAdapter(api_key="AIzaSyTEST1234567890")
    fc = SimpleNamespace(name="get_time", args={"tz": "UTC"})
    part = SimpleNamespace(text=None, function_call=fc)
    content = SimpleNamespace(parts=[part])
    cand = SimpleNamespace(content=content, finish_reason=SimpleNamespace(name="STOP"))
    response = SimpleNamespace(
        candidates=[cand],
        usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5, cached_content_token_count=0),
        model_version="gemini-1.5-pro-latest",
    )
    resp = adapter._build_response(response, "gemini-1.5-pro")
    assert resp.content == ""
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "get_time"
    assert resp.tool_calls[0].arguments == {"tz": "UTC"}
    assert resp.tool_calls[0].id.startswith("gemini_")
    # Tool call present → stop_reason MUST be tool_use regardless of finish_reason.
    assert resp.stop_reason == "tool_use"


def test_gemini_build_response_text_only():
    adapter = GeminiAdapter(api_key="AIzaSyTEST1234567890")
    part = SimpleNamespace(text="Hello from Gemini", function_call=None)
    content = SimpleNamespace(parts=[part])
    cand = SimpleNamespace(content=content, finish_reason=SimpleNamespace(name="STOP"))
    response = SimpleNamespace(
        candidates=[cand],
        usage_metadata=SimpleNamespace(prompt_token_count=5, candidates_token_count=2, cached_content_token_count=0),
        model_version="gemini-1.5-pro",
    )
    resp = adapter._build_response(response, "gemini-1.5-pro")
    assert resp.content == "Hello from Gemini"
    assert resp.tool_calls == []
    assert resp.stop_reason == "end_turn"
