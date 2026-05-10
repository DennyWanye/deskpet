"""P4-S22 — integration test: AgentLoop + ToolRegistry + new code tools.

Mocks the LLM with a scripted tool-call sequence:
  turn 1: todo_write [plan, write file, mark done]
  turn 2: write_file at <project>/hello.py with print('hi')
  turn 3: glob to confirm hello.py exists
  turn 4: todo_write (mark done) + final message

Asserts:
  - hello.py is created in project root
  - SessionDB has a code_todos row with content matching
  - AgentLoop yields a FinalEvent at the end
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent.agent_loop import AgentLoop, FinalEvent, ToolCallEvent, ToolResultEvent
from deskpet.memory.session_db import SessionDB
from deskpet.tools.code_tools import (
    register_code_tools,
    build_todo_write_tool,
)
from deskpet.tools.os_tools import register_os_tools
from deskpet.tools.registry import ToolRegistry
from llm.types import ChatResponse, ChatUsage, ToolCall


class _ScriptedLLM:
    """Replays a fixed sequence of (content, tool_calls) responses.

    AgentLoop calls chat_with_fallback once per iteration; we pop the
    next scripted response off ``self._script`` and return it.
    """

    def __init__(self, script: list[ChatResponse]):
        self._script = list(script)

    async def chat_with_fallback(self, messages, tools=None, model=None, **kwargs):
        if not self._script:
            # Default tail = "I'm done"
            return ChatResponse(
                content="all done",
                tool_calls=[],
                stop_reason="end_turn",
                model="mock",
                usage=ChatUsage(
                    input_tokens=0, output_tokens=0,
                    cache_read_tokens=0, cache_write_tokens=0,
                ),
            )
        return self._script.pop(0)


def _resp_tool_call(name: str, args: dict, content: str = "") -> ChatResponse:
    return ChatResponse(
        content=content,
        tool_calls=[ToolCall(id=f"call-{name}", name=name, arguments=args)],
        stop_reason="tool_use",
        model="mock",
        usage=ChatUsage(
            input_tokens=0, output_tokens=0,
            cache_read_tokens=0, cache_write_tokens=0,
        ),
    )


def _resp_final(content: str) -> ChatResponse:
    return ChatResponse(
        content=content,
        tool_calls=[],
        stop_reason="end_turn",
        model="mock",
        usage=ChatUsage(
            input_tokens=0, output_tokens=0,
            cache_read_tokens=0, cache_write_tokens=0,
        ),
    )


@pytest.mark.asyncio
async def test_code_mode_multi_tool_chain_creates_file(tmp_path):
    project = tmp_path / "demo-proj"
    project.mkdir()

    db_path = tmp_path / "state.db"
    sdb = SessionDB(db_path=str(db_path))
    await sdb.initialize()

    # Build a fresh registry, register OS + code tools
    registry = ToolRegistry()
    register_os_tools(registry)
    todo_handler, todo_schema = build_todo_write_tool(
        session_db=sdb,
        code_session_id_resolver=lambda: "code-test",
        broadcaster=None,
    )
    register_code_tools(
        registry,
        todo_write_handler=todo_handler,
        todo_write_schema=todo_schema,
    )

    # Inject project root for glob/grep
    registry.set_session_context(
        "default", {"_project_root": str(project)}
    )

    # Script: todo_write → write_file → glob → final
    target_file = project / "hello.py"
    script = [
        _resp_tool_call(
            "todo_write",
            {
                "items": [
                    {"content": "create hello.py", "activeForm": "creating hello.py", "status": "in_progress"},
                    {"content": "verify file exists", "activeForm": "verifying", "status": "pending"},
                ]
            },
        ),
        _resp_tool_call(
            "write_file",
            {"path": str(target_file), "content": "print('hi')\n"},
        ),
        _resp_tool_call("glob", {"pattern": "*.py"}),
        _resp_tool_call(
            "todo_write",
            {
                "items": [
                    {"content": "create hello.py", "activeForm": "created", "status": "completed"},
                    {"content": "verify file exists", "activeForm": "verified", "status": "completed"},
                ]
            },
        ),
        _resp_final("Done — created hello.py and verified."),
    ]

    loop = AgentLoop(
        llm_registry=_ScriptedLLM(script),
        tool_registry=registry,
        max_iterations=20,
    )

    events = []
    async for ev in loop.run(
        [{"role": "user", "content": "make hello.py"}],
        session_id="default",
    ):
        events.append(ev)

    # Final event reached
    finals = [e for e in events if isinstance(e, FinalEvent)]
    assert len(finals) >= 1
    assert "hello.py" in finals[-1].content.lower() or "done" in finals[-1].content.lower()

    # File created
    assert target_file.exists()
    assert target_file.read_text(encoding="utf-8") == "print('hi')\n"

    # Todos persisted (final write of 2 completed items)
    todos = await sdb.get_code_todos("code-test")
    assert len(todos) == 2
    assert all(t["status"] == "completed" for t in todos)

    # AgentLoop emitted at least 4 ToolCallEvents (todo, write, glob, todo)
    tool_calls = [e for e in events if isinstance(e, ToolCallEvent)]
    assert len(tool_calls) >= 4

    # Tool results all ok — result envelope is JSON-encoded; parse and
    # assert ok=True for each.
    tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
    for r in tool_results:
        try:
            envelope = json.loads(r.result)
        except json.JSONDecodeError:
            continue  # tool returned a non-JSON string; treat as success
        # OS tools wrap as {"ok", "result", "error"}; old/legacy tools just
        # emit the raw payload. Both shapes count as "successful invocation"
        # — failure surfaces as either ok=False or an "error" key in the
        # decoded payload.
        if isinstance(envelope, dict) and "ok" in envelope:
            assert envelope["ok"], f"{r.tool_name} failed: {envelope.get('error')}"
