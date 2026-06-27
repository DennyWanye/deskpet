from __future__ import annotations

import json
from typing import Any

import pytest

from agent.agent_loop import AgentLoop, FinalEvent
from llm.types import ChatResponse, ToolCall


class _Tools:
    def schemas(self, enabled_toolsets: Any = None) -> list[dict[str, Any]]:  # noqa: ARG002
        return [
            {
                "type": "function",
                "function": {
                    "name": "noop",
                    "description": "noop",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

    def dispatch(self, name: str, args: dict[str, Any], task_id: str) -> str:  # noqa: ARG002
        return json.dumps({"ok": True}, ensure_ascii=False)


class _LoopingLLM:
    def __init__(self, *, stop_after: int) -> None:
        self.stop_after = stop_after
        self.calls = 0
        self.prompts: list[list[dict[str, Any]]] = []

    async def chat_with_fallback(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.calls += 1
        self.prompts.append([dict(m) for m in messages])
        stop = "end_turn" if self.calls >= self.stop_after else "tool_use"
        return ChatResponse(
            content=f"turn {self.calls}",
            stop_reason=stop,
            model="stub-model",
            tool_calls=[
                ToolCall(id="call_1", name="noop", arguments={"value": self.calls})
            ] if stop == "tool_use" else [],
        )


async def _collect(agen):
    out = []
    async for ev in agen:
        out.append(ev)
    return out


def _todo_sync_messages(prompt: list[dict[str, Any]]) -> list[str]:
    return [
        str(m.get("content") or "")
        for m in prompt
        if m.get("role") == "system"
        and str(m.get("content") or "").startswith("[当前任务进度]")
    ]


@pytest.mark.asyncio
async def test_todo_sync_injected_every_n() -> None:
    llm = _LoopingLLM(stop_after=8)

    async def getter(session_id: str) -> list[dict[str, Any]]:
        assert session_id == "sid-1"
        return [
            {"content": "write tests", "status": "completed"},
            {"content": "implement focus chain", "status": "in_progress"},
            {"content": "run regression", "status": "pending"},
        ]

    loop = AgentLoop(
        llm,
        _Tools(),
        max_iterations=9,
        code_todo_getter=getter,
    )

    events = await _collect(
        loop.run([{"role": "user", "content": "go"}], session_id="sid-1")
    )

    assert any(isinstance(ev, FinalEvent) for ev in events)
    sync_messages = _todo_sync_messages(llm.prompts[7])
    assert len(sync_messages) == 1
    assert "✓ write tests" in sync_messages[0]
    assert "🔄 implement focus chain" in sync_messages[0]
    assert "⏳ run regression" in sync_messages[0]


@pytest.mark.asyncio
async def test_no_getter_no_injection() -> None:
    llm = _LoopingLLM(stop_after=8)
    loop = AgentLoop(llm, _Tools(), max_iterations=9, code_todo_getter=None)

    events = await _collect(
        loop.run([{"role": "user", "content": "go"}], session_id="sid-1")
    )

    assert any(isinstance(ev, FinalEvent) for ev in events)
    assert _todo_sync_messages(llm.prompts[7]) == []


@pytest.mark.asyncio
async def test_empty_todos_no_injection() -> None:
    llm = _LoopingLLM(stop_after=8)

    async def getter(session_id: str) -> list[dict[str, Any]]:  # noqa: ARG001
        return []

    loop = AgentLoop(
        llm,
        _Tools(),
        max_iterations=9,
        code_todo_getter=getter,
    )

    events = await _collect(
        loop.run([{"role": "user", "content": "go"}], session_id="sid-1")
    )

    assert any(isinstance(ev, FinalEvent) for ev in events)
    assert _todo_sync_messages(llm.prompts[7]) == []


@pytest.mark.asyncio
async def test_todo_sync_suppressed_at_tier3() -> None:
    llm = _LoopingLLM(stop_after=32)
    getter_calls: list[str] = []

    async def getter(session_id: str) -> list[dict[str, Any]]:
        getter_calls.append(session_id)
        return [{"content": "finish the work", "status": "pending"}]

    loop = AgentLoop(
        llm,
        _Tools(),
        max_iterations=33,
        code_todo_getter=getter,
    )

    events = await _collect(
        loop.run([{"role": "user", "content": "go"}], session_id="sid-1")
    )

    assert any(isinstance(ev, FinalEvent) for ev in events)
    assert getter_calls == ["sid-1", "sid-1", "sid-1"]
    assert len(_todo_sync_messages(llm.prompts[31])) == 3
