"""Unit tests for agent.agent_loop. All LLM + tool calls are mocked.

Coverage:
    - happy path: llm-tool-llm-final event sequence
    - max_iterations: loop aborts gracefully
    - tool error normalization: tool exception → JSON error string
    - budget cap: loop aborts with error event
    - concurrent tool dispatch (spec §11.9)
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

import pytest

from agent.agent_loop import (
    AgentLoop,
    AssistantMessageEvent,
    ErrorEvent,
    FinalEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from agent.task_id import new_task_id
from llm.budget import DailyBudget
from llm.errors import LLMProviderError
from llm.types import ChatResponse, ChatUsage, ToolCall


class FakeLLMRegistry:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def chat_with_fallback(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.calls.append({"messages": messages, "tools": tools, "model": model})
        if not self._responses:
            raise AssertionError("FakeLLMRegistry ran out of programmed responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeToolRegistry:
    def __init__(self, schemas: Optional[list[dict[str, Any]]] = None, handlers: Optional[dict[str, Any]] = None) -> None:
        self._schemas = schemas or []
        self._handlers = handlers or {}
        self.calls: list[dict[str, Any]] = []

    def schemas(self, enabled_toolsets: Optional[list[str]] = None) -> list[dict[str, Any]]:
        return list(self._schemas)

    def dispatch(self, name: str, args: dict[str, Any], task_id: str) -> Any:
        self.calls.append({"name": name, "args": args, "task_id": task_id})
        if name not in self._handlers:
            raise KeyError(f"tool {name!r} not registered")
        return self._handlers[name](args)


# ───────────────────── tests ─────────────────────


def test_task_id_format():
    tid = new_task_id()
    assert tid.startswith("task_")
    # task_<YYMMDDHHMMSS>_<8 hex>
    parts = tid.split("_")
    assert len(parts) == 3
    assert len(parts[1]) == 12
    assert len(parts[2]) == 8


@pytest.mark.asyncio
async def test_happy_path_no_tools():
    llm = FakeLLMRegistry(
        responses=[
            ChatResponse(
                content="Hello, world!",
                stop_reason="end_turn",
                usage=ChatUsage(input_tokens=5, output_tokens=3),
                model="claude-sonnet-4-5",
            )
        ]
    )
    tools = FakeToolRegistry()
    loop = AgentLoop(llm_registry=llm, tool_registry=tools, max_iterations=5)
    events = [e async for e in loop.run(messages=[{"role": "user", "content": "Hi"}])]
    kinds = [e.type for e in events]
    assert kinds == ["assistant_message", "final"]
    final = events[-1]
    assert isinstance(final, FinalEvent)
    assert final.content == "Hello, world!"
    assert final.total_input_tokens == 5
    assert final.total_output_tokens == 3


@pytest.mark.asyncio
async def test_happy_path_one_tool_use_then_final():
    llm = FakeLLMRegistry(
        responses=[
            ChatResponse(
                content="Let me check...",
                tool_calls=[ToolCall(id="call_1", name="get_time", arguments={})],
                stop_reason="tool_use",
                usage=ChatUsage(input_tokens=20, output_tokens=10),
                model="claude-sonnet-4-5",
            ),
            ChatResponse(
                content="It's 10:30.",
                stop_reason="end_turn",
                usage=ChatUsage(input_tokens=30, output_tokens=5),
                model="claude-sonnet-4-5",
            ),
        ]
    )
    tools = FakeToolRegistry(
        schemas=[
            {
                "type": "function",
                "function": {"name": "get_time", "description": "current time", "parameters": {"type": "object"}},
            }
        ],
        handlers={"get_time": lambda args: {"time": "10:30"}},
    )
    loop = AgentLoop(llm_registry=llm, tool_registry=tools)
    events = [e async for e in loop.run(messages=[{"role": "user", "content": "What time is it?"}])]
    kinds = [e.type for e in events]
    assert kinds == [
        "assistant_message",
        "tool_call",
        "tool_result",
        "assistant_message",
        "final",
    ]
    # Verify tool_result is a JSON string per §5 contract.
    tool_res = next(e for e in events if isinstance(e, ToolResultEvent))
    assert json.loads(tool_res.result) == {"time": "10:30"}
    # Second LLM call MUST see the tool result in its messages.
    second_messages = llm.calls[1]["messages"]
    assert any(m.get("role") == "tool" for m in second_messages)
    # Totals are aggregated across iterations.
    final = events[-1]
    assert isinstance(final, FinalEvent)
    assert final.total_input_tokens == 50
    assert final.total_output_tokens == 15


@pytest.mark.asyncio
async def test_concurrent_tool_dispatch():
    """Multiple tool_calls in one turn MUST run concurrently (§11.9)."""

    sleep_event = asyncio.Event()
    start_times: dict[str, float] = {}
    end_times: dict[str, float] = {}

    async def slow_tool(args):
        name = args.get("tag", "?")
        start_times[name] = asyncio.get_event_loop().time()
        await asyncio.sleep(0.05)
        end_times[name] = asyncio.get_event_loop().time()
        return {"tag": name}

    # FakeToolRegistry.dispatch returns whatever handler returns. Make the
    # handler return a coroutine so _dispatch_tool awaits it.
    def handler_a(args):
        return slow_tool({"tag": "A"})

    def handler_b(args):
        return slow_tool({"tag": "B"})

    llm = FakeLLMRegistry(
        responses=[
            ChatResponse(
                content="",
                tool_calls=[
                    ToolCall(id="c1", name="tool_a", arguments={}),
                    ToolCall(id="c2", name="tool_b", arguments={}),
                ],
                stop_reason="tool_use",
                usage=ChatUsage(),
            ),
            ChatResponse(content="done", stop_reason="end_turn", usage=ChatUsage()),
        ]
    )
    tools = FakeToolRegistry(
        schemas=[],
        handlers={"tool_a": handler_a, "tool_b": handler_b},
    )
    loop = AgentLoop(llm_registry=llm, tool_registry=tools)
    t0 = asyncio.get_event_loop().time()
    events = [e async for e in loop.run(messages=[{"role": "user", "content": "go"}])]
    total = asyncio.get_event_loop().time() - t0
    # Serial would be ~0.10s, parallel closer to 0.05s. Give generous margin.
    assert total < 0.09
    assert {e.type for e in events} == {
        "assistant_message",
        "tool_call",
        "tool_result",
        "final",
    } or {"assistant_message", "tool_call", "tool_result", "final"}.issubset({e.type for e in events})


@pytest.mark.asyncio
async def test_tool_exception_produces_error_json():
    def broken_tool(args):
        raise ValueError("kaboom")

    llm = FakeLLMRegistry(
        responses=[
            ChatResponse(
                content="",
                tool_calls=[ToolCall(id="c1", name="broken", arguments={})],
                stop_reason="tool_use",
                usage=ChatUsage(),
            ),
            ChatResponse(content="recovered", stop_reason="end_turn", usage=ChatUsage()),
        ]
    )
    tools = FakeToolRegistry(handlers={"broken": broken_tool})
    loop = AgentLoop(llm_registry=llm, tool_registry=tools)
    events = [e async for e in loop.run(messages=[{"role": "user", "content": "x"}])]
    res = next(e for e in events if isinstance(e, ToolResultEvent))
    parsed = json.loads(res.result)
    assert "error" in parsed
    assert parsed["retriable"] is False
    assert "ValueError" in parsed["error"]
    # Loop continued and produced a final turn despite the error.
    final = events[-1]
    assert isinstance(final, FinalEvent)
    assert final.content == "recovered"


@pytest.mark.asyncio
async def test_max_iterations_reached():
    # LLM keeps asking for a tool forever.
    never_ending = [
        ChatResponse(
            content="",
            tool_calls=[ToolCall(id=f"call_{i}", name="ping", arguments={})],
            stop_reason="tool_use",
            usage=ChatUsage(),
        )
        for i in range(30)
    ]
    llm = FakeLLMRegistry(responses=never_ending)
    tools = FakeToolRegistry(handlers={"ping": lambda args: {"pong": True}})
    loop = AgentLoop(llm_registry=llm, tool_registry=tools, max_iterations=3)
    events = [e async for e in loop.run(messages=[{"role": "user", "content": "loop"}])]
    assert isinstance(events[-1], ErrorEvent)
    assert events[-1].reason == "max_iterations"


@pytest.mark.asyncio
async def test_budget_exceeded_aborts_with_error(tmp_path):
    budget = DailyBudget(cap_usd=0.001, state_path=tmp_path / "b.json")
    # Pre-seed with spend above cap.
    budget.add_usage("openai", "gpt-4o", ChatUsage(output_tokens=100_000))  # $1
    assert budget.check_allowed() is False

    llm = FakeLLMRegistry(
        responses=[
            ChatResponse(content="unreachable", stop_reason="end_turn", usage=ChatUsage()),
        ]
    )
    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=FakeToolRegistry(),
        budget_checker=budget,
    )
    events = [e async for e in loop.run(messages=[{"role": "user", "content": "x"}])]
    assert isinstance(events[0], ErrorEvent)
    assert events[0].reason == "budget_exceeded"
    assert len(llm.calls) == 0  # MUST NOT invoke LLM when over budget


@pytest.mark.asyncio
async def test_llm_error_surfaces_as_error_event():
    llm = FakeLLMRegistry(
        responses=[LLMProviderError("all providers failed: gone")]
    )
    loop = AgentLoop(llm_registry=llm, tool_registry=FakeToolRegistry())
    events = [e async for e in loop.run(messages=[{"role": "user", "content": "x"}])]
    assert len(events) == 1
    assert isinstance(events[0], ErrorEvent)
    assert events[0].reason == "llm_error"
    assert "all providers failed" in events[0].detail


@pytest.mark.asyncio
async def test_tool_result_echoes_back_to_llm_messages():
    """The second LLM call MUST see role='tool' with the tool result."""
    llm = FakeLLMRegistry(
        responses=[
            ChatResponse(
                content="",
                tool_calls=[ToolCall(id="c1", name="echo", arguments={"x": 1})],
                stop_reason="tool_use",
                usage=ChatUsage(),
            ),
            ChatResponse(content="ok", stop_reason="end_turn", usage=ChatUsage()),
        ]
    )
    tools = FakeToolRegistry(handlers={"echo": lambda args: {"seen": args}})
    loop = AgentLoop(llm_registry=llm, tool_registry=tools)
    [e async for e in loop.run(messages=[{"role": "user", "content": "x"}])]
    second_msgs = llm.calls[1]["messages"]
    tool_msgs = [m for m in second_msgs if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "c1"
    assert tool_msgs[0]["name"] == "echo"
    assert json.loads(tool_msgs[0]["content"]) == {"seen": {"x": 1}}
