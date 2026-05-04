"""P4-S20 Wave 1b: agent loop tool_use TDD tests.

Verifies that AgentLoop routes through ToolRegistry.execute_tool (which
applies permission gating) when the registry advertises v2 protocol.
Falls back to legacy dispatch() when only v1 is available.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pytest

from agent.agent_loop import AgentLoop, FinalEvent, ToolCallEvent, ToolResultEvent
from llm.types import ChatResponse, ChatUsage as Usage, ToolCall
from deskpet.tools.registry import ToolRegistry
from deskpet.tools.os_tools import register_os_tools
from deskpet.permissions.gate import PermissionGate, PermissionGateConfig
from deskpet.types.skill_platform import PermissionResponse


# --------------------------------------------------------------
# Stub LLM that returns a scripted sequence of ChatResponses
# --------------------------------------------------------------


class _ScriptedLLM:
    """Yields a queue of pre-baked ChatResponse objects."""

    def __init__(self, responses: list[ChatResponse]) -> None:
        self._responses = list(responses)

    async def chat_with_fallback(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        if not self._responses:
            raise RuntimeError("scripted LLM exhausted")
        return self._responses.pop(0)


def _tool_use_response(tool_name: str, args: dict[str, Any]) -> ChatResponse:
    return ChatResponse(
        content="",
        tool_calls=[
            ToolCall(id="call_1", name=tool_name, arguments=args),
        ],
        stop_reason="tool_use",
        model="stub",
        usage=Usage(input_tokens=10, output_tokens=10),
    )


def _final_response(text: str) -> ChatResponse:
    return ChatResponse(
        content=text,
        tool_calls=[],
        stop_reason="end_turn",
        model="stub",
        usage=Usage(input_tokens=5, output_tokens=10),
    )


# --------------------------------------------------------------
# Tests
# --------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_loop_uses_execute_tool_when_available(tmp_path) -> None:
    """When registry.execute_tool exists, AgentLoop must route through it."""
    reg = ToolRegistry()
    register_os_tools(reg)

    gate = PermissionGate(config=PermissionGateConfig(timeout_s=0.5))

    async def allow(req):
        return PermissionResponse(request_id=req.request_id, decision="allow")

    gate.set_responder(allow)
    reg.set_permission_gate(gate)

    target = tmp_path / "todo.txt"
    llm = _ScriptedLLM(
        [
            _tool_use_response(
                "write_file",
                {"path": str(target), "content": "milk"},
            ),
            _final_response("Done — wrote the file."),
        ]
    )

    loop = AgentLoop(llm_registry=llm, tool_registry=reg, max_iterations=5)

    events = []
    async for ev in loop.run([{"role": "user", "content": "create todo"}], session_id="s1"):
        events.append(ev)

    # Must have ToolCall + ToolResult + Final
    kinds = [type(e).__name__ for e in events]
    assert "ToolCallEvent" in kinds
    assert "ToolResultEvent" in kinds
    assert "FinalEvent" in kinds

    # File was actually written (i.e. handler ran)
    assert target.read_text(encoding="utf-8") == "milk"


@pytest.mark.asyncio
async def test_agent_loop_respects_permission_deny(tmp_path) -> None:
    """When PermissionGate denies, the handler must NOT run."""
    reg = ToolRegistry()
    register_os_tools(reg)

    gate = PermissionGate(config=PermissionGateConfig(timeout_s=0.3))

    async def deny(req):
        return PermissionResponse(request_id=req.request_id, decision="deny")

    gate.set_responder(deny)
    reg.set_permission_gate(gate)

    target = tmp_path / "should_not_exist.txt"
    llm = _ScriptedLLM(
        [
            _tool_use_response(
                "write_file",
                {"path": str(target), "content": "blocked"},
            ),
            _final_response("Sorry, you denied the write."),
        ]
    )

    loop = AgentLoop(llm_registry=llm, tool_registry=reg, max_iterations=3)

    tool_results = []
    async for ev in loop.run([{"role": "user", "content": "x"}], session_id="s1"):
        if isinstance(ev, ToolResultEvent):
            tool_results.append(ev)

    assert tool_results, "must have at least one tool result"
    payload = json.loads(tool_results[0].result)
    assert payload["ok"] is False
    assert "permission denied" in payload["error"]
    assert not target.exists()


@pytest.mark.asyncio
async def test_agent_loop_legacy_registry_still_works() -> None:
    """Registry without execute_tool falls back to legacy dispatch path."""

    class _LegacyReg:
        """Mimics pre-v2 registry: only dispatch() and schemas()."""

        def __init__(self) -> None:
            self.dispatched: list[str] = []

        def schemas(self, enabled_toolsets=None):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "echo",
                        "description": "echo",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]

        def dispatch(self, name: str, args: dict, task_id: str) -> str:
            self.dispatched.append(name)
            return json.dumps({"echoed": args})

    reg = _LegacyReg()
    llm = _ScriptedLLM(
        [
            _tool_use_response("echo", {"hi": "there"}),
            _final_response("ok"),
        ]
    )
    loop = AgentLoop(llm_registry=llm, tool_registry=reg, max_iterations=3)

    final = None
    async for ev in loop.run([{"role": "user", "content": "x"}]):
        if isinstance(ev, FinalEvent):
            final = ev
    assert final is not None
    assert reg.dispatched == ["echo"]


@pytest.mark.asyncio
async def test_agent_loop_max_iterations_aborts() -> None:
    """If LLM keeps requesting tools, loop must abort with error."""
    reg = ToolRegistry()
    register_os_tools(reg)
    gate = PermissionGate(config=PermissionGateConfig(timeout_s=0.2))

    async def allow(req):
        return PermissionResponse(request_id=req.request_id, decision="allow")

    gate.set_responder(allow)
    reg.set_permission_gate(gate)

    # Build a long script of tool calls + final
    script = [
        _tool_use_response(
            "list_directory", {"path": "."}
        )
        for _ in range(10)
    ]
    llm = _ScriptedLLM(script)
    loop = AgentLoop(llm_registry=llm, tool_registry=reg, max_iterations=3)

    error = None
    async for ev in loop.run([{"role": "user", "content": "x"}], session_id="s1"):
        if hasattr(ev, "reason") and getattr(ev, "reason", "") == "max_iterations":
            error = ev
    assert error is not None
