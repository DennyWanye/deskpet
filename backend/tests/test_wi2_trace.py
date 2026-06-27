from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent.agent_loop import AgentLoop, FinalEvent, ToolResultEvent
from agent.trace import IterationTracer
from llm.types import ChatResponse, ToolCall


class _Tools:
    def __init__(self, marker: str | None = None) -> None:
        self.marker = marker

    def schemas(self, enabled_toolsets: Any = None) -> list[dict]:  # noqa: ARG002
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

    def dispatch(self, name: str, args: dict, task_id: str) -> str:  # noqa: ARG002
        return json.dumps({"ok": True, "echo": args}, ensure_ascii=False)


class _TraceLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def chat_with_fallback(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.calls += 1
        if self.calls == 1:
            return ChatResponse(
                content="using tool",
                stop_reason="tool_use",
                model="stub-model",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="echo",
                        arguments={
                            "full": "x" * 500,
                            "nested": {"keep": ["all", "args"]},
                        },
                    )
                ],
            )
        return ChatResponse(
            content="done",
            stop_reason="end_turn",
            model="stub-model",
        )


async def _collect(agen):
    out = []
    async for ev in agen:
        out.append(ev)
    return out


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.mark.asyncio
async def test_tracer_writes_jsonl_with_full_tool_args(tmp_path: Path) -> None:
    tracer = IterationTracer(
        trace_dir=tmp_path / "traces",
        session_id="session-a",
        task_id="task-a",
    )
    llm = _TraceLLM()
    loop = AgentLoop(llm, _Tools(), max_iterations=3, tracer=tracer)

    events = await _collect(
        loop.run(
            [{"role": "user", "content": "trace this"}],
            task_id="task-a",
            session_id="session-a",
        )
    )

    assert any(isinstance(ev, ToolResultEvent) for ev in events)
    assert any(isinstance(ev, FinalEvent) for ev in events)

    rows = _read_jsonl(tmp_path / "traces" / "task-a.jsonl")
    kinds = [row["kind"] for row in rows]
    assert "iter_start" in kinds
    assert "llm_out" in kinds
    assert "tool_result" in kinds
    assert "end" in kinds

    llm_out = next(row for row in rows if row["kind"] == "llm_out" and row["tool_calls"])
    args = llm_out["tool_calls"][0]["args"]
    assert args["full"] == "x" * 500
    assert args["nested"] == {"keep": ["all", "args"]}

    tool_result = next(row for row in rows if row["kind"] == "tool_result")
    assert tool_result["args"]["full"] == "x" * 500
    assert len(tool_result["result_preview"]) <= 200


@pytest.mark.asyncio
async def test_tracer_none_creates_no_trace_file(tmp_path: Path) -> None:
    llm = _TraceLLM()
    loop = AgentLoop(llm, _Tools(), max_iterations=3, tracer=None)

    events = await _collect(
        loop.run(
            [{"role": "user", "content": "no trace"}],
            task_id="task-none",
            session_id="session-none",
        )
    )

    assert any(isinstance(ev, FinalEvent) for ev in events)
    assert not (tmp_path / "traces").exists()
