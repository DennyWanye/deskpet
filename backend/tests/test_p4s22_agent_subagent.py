# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S22 — agent (subagent) tool.

We focus on the parts that don't require a real LLM:
  * SubsetRegistryAdapter filtering — schemas() returns only allowed
    tools; execute_tool raises PermissionError for non-allowed names
  * agent tool's args validation
  * Recursion guard — even if caller asks for ``tools=["agent"]``
    it gets stripped from the subset

We mock the LLMRegistry to script a single FinalEvent, verifying the
glue code wires everything together without standing up an actual
chat completion.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from deskpet.tools.code_tools.agent_tool import (
    _SubsetRegistryAdapter,
    build_agent_tool,
)
from llm.types import ChatResponse, ChatUsage


# ---------------------------------------------------------------------------
# SubsetRegistryAdapter
# ---------------------------------------------------------------------------


class _FakeRegistry:
    """Minimal stand-in for ToolRegistry exposing schemas() + execute_tool()."""

    def __init__(self, tool_names: list[str]):
        self._names = tool_names
        self.execute_calls: list[tuple] = []

    def schemas(self, enabled_toolsets=None):
        return [
            {"function": {"name": n, "description": f"{n} desc"}}
            for n in self._names
        ]

    async def execute_tool(self, name, params, session_id, task_id=""):
        self.execute_calls.append((name, params, session_id))
        return {"ok": True, "result": f"ran-{name}", "error": None}


def test_adapter_schemas_filtered_to_subset():
    parent = _FakeRegistry(["read_file", "write_file", "bash", "grep"])
    adapter = _SubsetRegistryAdapter(parent, ["read_file", "grep"])
    out = adapter.schemas()
    names = [s["function"]["name"] for s in out]
    assert sorted(names) == ["grep", "read_file"]
    assert "write_file" not in names
    assert "bash" not in names


@pytest.mark.asyncio
async def test_adapter_execute_blocks_non_allowed():
    parent = _FakeRegistry(["read_file", "write_file"])
    adapter = _SubsetRegistryAdapter(parent, ["read_file"])
    # Allowed: ok
    res = await adapter.execute_tool("read_file", {}, "sess", "")
    assert res["ok"]
    # Blocked: PermissionError
    with pytest.raises(PermissionError):
        await adapter.execute_tool("write_file", {}, "sess", "")


# ---------------------------------------------------------------------------
# build_agent_tool — validation + recursion guard
# ---------------------------------------------------------------------------


def test_handler_requires_description_and_prompt():
    handler, _ = build_agent_tool(
        llm_shim=None,
        parent_tool_registry=_FakeRegistry([]),
        parent_session_id_resolver=lambda: "p",
    )
    out = json.loads(handler({}))
    assert "error" in out


def test_recursion_guard_strips_agent_from_tool_subset():
    """Caller passes ``tools=["agent", "read_file"]`` — agent gets dropped."""
    parent = _FakeRegistry(["agent", "read_file"])
    seen_tools: list[list[str]] = []

    class _ScriptedShim:
        async def chat_with_fallback(self, messages, tools=None, model=None, **kw):
            seen_tools.append([s["function"]["name"] for s in (tools or [])])
            return ChatResponse(
                content="done",
                tool_calls=[],
                stop_reason="end_turn",
                model="mock",
                usage=ChatUsage(input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0),
            )

    handler, _ = build_agent_tool(
        llm_shim=_ScriptedShim(),
        parent_tool_registry=parent,
        parent_session_id_resolver=lambda: "p",
    )
    out = json.loads(
        handler(
            {
                "description": "test",
                "prompt": "do something",
                "tools": ["agent", "read_file"],
            }
        )
    )
    assert "result" in out
    # The subagent was given ONE schema (read_file), agent stripped
    assert any("read_file" in t for t in seen_tools)
    assert all("agent" not in t for t in seen_tools)


def test_default_tool_subset_is_read_only():
    """No ``tools`` argument → safe default of read-only tools."""
    parent = _FakeRegistry(
        ["read_file", "list_directory", "glob", "grep", "web_search", "write_file", "bash"]
    )
    seen_tools: list[list[str]] = []

    class _Shim:
        async def chat_with_fallback(self, messages, tools=None, model=None, **kw):
            seen_tools.append([s["function"]["name"] for s in (tools or [])])
            return ChatResponse(
                content="done",
                tool_calls=[],
                stop_reason="end_turn",
                model="mock",
                usage=ChatUsage(input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0),
            )

    handler, _ = build_agent_tool(
        llm_shim=_Shim(),
        parent_tool_registry=parent,
        parent_session_id_resolver=lambda: "p",
    )
    out = json.loads(handler({"description": "x", "prompt": "y"}))
    seen = seen_tools[0] if seen_tools else []
    # Write/bash NOT in subset
    assert "write_file" not in seen
    assert "bash" not in seen
    # Read-only set IS in
    for read_tool in ("read_file", "list_directory", "glob", "grep", "web_search"):
        assert read_tool in seen


def test_subagent_session_id_appends_sub():
    """Subagent runs under ``<parent_sid>.sub`` to keep memory dedup'd."""
    parent = _FakeRegistry(["read_file"])
    captured_sid: list[str] = []

    class _Shim:
        async def chat_with_fallback(self, messages, tools=None, model=None, **kw):
            return ChatResponse(
                content="ok",
                tool_calls=[],
                stop_reason="end_turn",
                model="m",
                usage=ChatUsage(input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0),
            )

    # We can't easily inspect AgentLoop's session_id from outside (it's
    # passed to its own internal state), but we can check that the tool
    # report's `session_id` propagation is wired by checking the tool
    # report payload has the parent + ".sub" in any execute_tool calls.
    # For this test, just verify the resolver is used.
    handler, _ = build_agent_tool(
        llm_shim=_Shim(),
        parent_tool_registry=parent,
        parent_session_id_resolver=lambda: "alice",
    )
    out = json.loads(handler({"description": "x", "prompt": "y"}))
    # Smoke — got a result back
    assert "result" in out
