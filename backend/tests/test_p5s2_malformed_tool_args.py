# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P5-S2 (2026-05-11): malformed tool_call.arguments JSON handling.

Regression: deepseek-v4-pro on the relay occasionally emits broken JSON
for tool_call.arguments when the args contain large markdown content
(unescaped \\n / \\" / \\\\ in long strings). The old code path
silently swallowed the JSONDecodeError and dispatched the tool with
``args = {}``. Tools then reported "missing required parameter", the
LLM retried with the same broken JSON, and after 3 strikes the
circuit breaker fired — burning tokens + popup-ing the user without
ever telling the model what was wrong.

Now: provider stashes the raw buffer + parse error on the assembled
tool_call dict; ChatResponse.ToolCall carries those fields; AgentLoop
short-circuits ``_dispatch_tool`` and returns a structured error
tool_result that tells the LLM exactly what JSON syntax error it made,
so it can regenerate.

Also covers: connect timeout bumped from 5s to 10s after seeing real
ConnectTimeout failures while the relay reported no errors (TLS handshake
on Windows occasionally takes 6-8s on first connect).
"""
from __future__ import annotations

import json

import httpx
import pytest

from agent.agent_loop import AgentLoop, ToolCall
from agent.tool_use_shim import _raw_to_response
from llm.types import ChatResponse, ChatUsage
from providers.openai_compatible import OpenAICompatibleProvider


# ─────────────── connect timeout (Fix A) ───────────────


def test_default_connect_timeout_is_10s():
    """Regression: 2026-05-11 — the relay never saw the request because our
    5s connect timeout fired before TLS handshake finished. Bumped to 10s.

    P5-S2 F1 (2026-05-12) — read budget bumped 60→120 per the relay's
    integration guide (docs/中转站建议.md): their PR #27 ships an SSE
    keep-alive comment every 15s that resets read timers across all
    hops, so the larger budget covers slow individual reasoning tokens
    (5KB+ chunks) without false-failing on healthy long generations.
    """
    p = OpenAICompatibleProvider(
        base_url="https://example.test", api_key="k", model="m"
    )
    # `httpx.Timeout` carries connect/read/write/pool fields.
    assert isinstance(p.timeout, httpx.Timeout)
    assert p.timeout.connect == 10.0, (
        f"connect timeout regressed to {p.timeout.connect}s; expected 10.0"
    )
    # F1: read budget covers thinking-mode bursts (the relay keep-alive
    # makes 120s safe even when upstream is silent for 30-60s mid-think).
    assert p.timeout.read == 120.0


# ─────────────── malformed args end-to-end (Fix B) ───────────────


def _stub_response_with_malformed_tool_call(args_raw: str) -> dict:
    """Build the dict shape provider.chat_with_tools would normally
    return when the model emits broken JSON args. Crucially, it
    populates ``_args_raw`` + ``_args_parse_error`` like the real
    assembly path does after a JSONDecodeError on args_buf."""
    return {
        "content": "",
        "reasoning_content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "name": "write_file",
                "arguments": {},  # Empty because parse failed
                "_args_raw": args_raw,
                "_args_parse_error": "Expecting ',' delimiter: line 1 column 50 (char 49)",
            }
        ],
        "stop_reason": "tool_use",
        "model": "deepseek-v4-pro",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def test_raw_to_response_propagates_parse_error_metadata():
    raw = _stub_response_with_malformed_tool_call('{"path": "fo')
    resp = _raw_to_response(raw)
    assert isinstance(resp, ChatResponse)
    assert len(resp.tool_calls) == 1
    tc = resp.tool_calls[0]
    assert tc.args_parse_error is not None
    assert "Expecting" in tc.args_parse_error
    assert tc.args_raw == '{"path": "fo'
    # Empty parsed args (since parse failed)
    assert tc.arguments == {}


@pytest.mark.asyncio
async def test_dispatch_short_circuits_on_malformed_args():
    """When AgentLoop._dispatch_tool sees a ToolCall with
    args_parse_error set, it MUST NOT invoke the registry and instead
    return a structured error result that the LLM can read."""

    class _SpyRegistry:
        def __init__(self):
            self.execute_calls: list[tuple] = []
            self.dispatch_calls: list[tuple] = []

        def schemas(self, **_):
            return []

        async def execute_tool(self, name, args, sid, task_id):
            self.execute_calls.append((name, args, sid, task_id))
            return {"ok": True, "result": "should not be reached"}

        def dispatch(self, name, args, task_id):
            self.dispatch_calls.append((name, args, task_id))
            return "should not be reached"

    reg = _SpyRegistry()

    # Minimal LLM stub — AgentLoop ctor needs it but we won't call run().
    class _DummyLLM:
        async def chat_with_fallback(self, *a, **k):
            raise NotImplementedError

    loop = AgentLoop(llm_registry=_DummyLLM(), tool_registry=reg)
    bad_tc = ToolCall(
        id="c1",
        name="write_file",
        arguments={},
        args_parse_error="Expecting ',' delimiter: line 1 column 50",
        args_raw='{"path": "G:\\\\foo", "content": "broken \\\\n stuff',
    )

    result_str = await loop._dispatch_tool(bad_tc, "task1", "session1")

    # Registry must NOT have been invoked
    assert reg.execute_calls == [], "execute_tool was called despite malformed args"
    assert reg.dispatch_calls == [], "dispatch was called despite malformed args"

    # Result must be structured + actionable
    parsed = json.loads(result_str)
    assert parsed["ok"] is False
    assert parsed["error"] == "tool_call_args_malformed_json"
    assert parsed["tool"] == "write_file"
    assert "JSON" in parsed["hint"]
    assert "Expecting" in parsed["parse_error"]
    assert "args_raw_preview" in parsed
    assert parsed["args_len"] == len(bad_tc.args_raw)


@pytest.mark.asyncio
async def test_dispatch_works_normally_when_args_parse_ok():
    """Regression guard: well-formed tool_calls should still go through
    the registry as before — the short-circuit MUST NOT interfere."""

    class _RealRegistry:
        def __init__(self):
            self.calls: list[tuple] = []

        def schemas(self, **_):
            return []

        async def execute_tool(self, name, args, sid, task_id):
            self.calls.append((name, args, sid, task_id))
            return {"ok": True, "result": '{"wrote": 42}'}

    reg = _RealRegistry()

    class _DummyLLM:
        async def chat_with_fallback(self, *a, **k):
            raise NotImplementedError

    loop = AgentLoop(llm_registry=_DummyLLM(), tool_registry=reg)
    good_tc = ToolCall(
        id="c1", name="write_file",
        arguments={"path": "/tmp/x", "content": "hi"},
        # Both metadata fields None → not malformed
    )
    result_str = await loop._dispatch_tool(good_tc, "task1", "session1")

    # Registry WAS invoked
    assert len(reg.calls) == 1
    assert reg.calls[0][0] == "write_file"
    assert reg.calls[0][1] == {"path": "/tmp/x", "content": "hi"}
    parsed = json.loads(result_str)
    assert parsed["ok"] is True


def test_assembled_tool_call_omits_metadata_when_args_parse_ok():
    """When args_buf parses cleanly, the assembled tool_call dict must
    NOT contain ``_args_raw`` / ``_args_parse_error`` keys — those exist
    only as the failure marker."""
    raw = {
        "content": "",
        "tool_calls": [
            {
                "id": "c1",
                "name": "read_file",
                "arguments": {"path": "/x"},
                # Note: NO _args_parse_error / _args_raw → clean parse
            }
        ],
        "stop_reason": "tool_use",
        "model": "m",
        "usage": {},
    }
    resp = _raw_to_response(raw)
    tc = resp.tool_calls[0]
    assert tc.args_parse_error is None
    assert tc.args_raw is None
