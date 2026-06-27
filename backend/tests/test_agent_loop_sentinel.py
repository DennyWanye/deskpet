# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

from typing import Any

import pytest

from agent.agent_loop import AgentLoop, FinalEvent, ToolCallEvent
from llm.types import ChatResponse, ChatUsage, ToolCall


class _ScriptedLLM:
    def __init__(self, responses: list[ChatResponse]) -> None:
        self._responses = list(responses)

    async def chat_with_fallback(self, messages: list[dict[str, Any]], **_: Any) -> ChatResponse:
        if not self._responses:
            raise AssertionError("LLM exhausted")
        return self._responses.pop(0)


class _Tools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    def schemas(self, enabled_toolsets: Any = None) -> list[dict]:
        return [
            {
                "name": "deepresearch",
                "parameters": {"type": "object", "properties": {"topic": {"type": "string"}}},
            }
        ]

    async def execute_tool(
        self,
        name: str,
        params: dict[str, Any],
        session_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        self.calls.append((name, dict(params), session_id))
        return {"ok": True, "result": "ran", "error": None}


def _deepresearch_response() -> ChatResponse:
    return ChatResponse(
        content="",
        tool_calls=[
            ToolCall(id="call_1", name="deepresearch", arguments={"topic": "old topic"})
        ],
        stop_reason="tool_use",
        usage=ChatUsage(),
        model="stub",
    )


def _final_response() -> ChatResponse:
    return ChatResponse(
        content="final",
        tool_calls=[],
        stop_reason="end_turn",
        usage=ChatUsage(),
        model="stub",
    )


@pytest.mark.asyncio
async def test_sentinel_run_refuses_deepresearch_without_dispatch():
    tools = _Tools()
    loop = AgentLoop(
        llm_registry=_ScriptedLLM([_deepresearch_response()]),
        tool_registry=tools,
    )

    events = [
        ev
        async for ev in loop.run(
            [{"role": "user", "content": "<<auto_resume>>"}],
            is_sentinel_run=True,
        )
    ]

    assert tools.calls == []
    finals = [ev for ev in events if isinstance(ev, FinalEvent)]
    assert len(finals) == 1
    assert "explicit" in finals[0].content.lower()
    assert "research" in finals[0].content.lower()


@pytest.mark.asyncio
async def test_normal_run_allows_deepresearch_dispatch():
    tools = _Tools()
    loop = AgentLoop(
        llm_registry=_ScriptedLLM([_deepresearch_response(), _final_response()]),
        tool_registry=tools,
    )

    events = [
        ev
        async for ev in loop.run(
            [{"role": "user", "content": "research rust"}],
            is_sentinel_run=False,
        )
    ]

    assert tools.calls == [("deepresearch", {"topic": "old topic"}, "default")]
    assert any(isinstance(ev, ToolCallEvent) for ev in events)

