# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Shared test fixtures for the p6-agent-loop-refactor change.

Phase 0 deliverable — the rest of the change relies on these helpers so
each phase's tests stay focused on the behaviour under test instead of
re-rolling LLM / registry mocks.

Public surface:
  * ``make_mock_llm_provider(responses)`` — async ``chat_with_tools``
    mock that yields a scripted sequence of raw response dicts.
  * ``make_mock_tool_registry(tools)`` — minimal v2 ToolRegistry stub
    exposing async ``execute_tool`` + ``schemas()``.
  * ``make_long_message_list(n=30)`` — fake conversation history used by
    compaction tests.
  * ``make_tool_call_event(name, args, id=None)`` — builds a tool_call
    dict matching what ``OpenAICompatibleProvider.chat_with_tools``
    returns (i.e. the raw shape ``tool_use_shim.OpenAICompatibleAgentLLM``
    converts into ``ToolCall``).
"""
from __future__ import annotations

import itertools
import json
import uuid
from typing import Any, Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock

ToolHandler = Callable[[dict[str, Any], str], Any]


def make_mock_llm_provider(responses: list[dict[str, Any]]) -> AsyncMock:
    """Build an ``AsyncMock`` whose ``chat_with_tools`` yields each
    response in ``responses`` sequentially.

    The shape mirrors ``OpenAICompatibleProvider.chat_with_tools`` —
    plain raw dicts, NOT ``ChatResponse`` pydantic models. The shim
    (``agent/tool_use_shim.py``) is responsible for the dict → model
    conversion, so fixtures stay on the wire-format side of that line.

    A response dict typically looks like::

        {
            "content": "ok",
            "tool_calls": [],                 # or [{id,name,arguments}]
            "stop_reason": "end_turn",        # or "tool_use"
            "model": "stub",
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
            },
        }

    Once the queue is drained, further calls raise ``RuntimeError`` so
    tests catch "LLM called more times than expected" bugs early.
    """
    queue = list(responses)

    async def _chat_with_tools(*args: Any, **kwargs: Any) -> dict[str, Any]:
        if not queue:
            raise RuntimeError(
                "mock LLM provider exhausted — chat_with_tools called more "
                "times than responses were scripted"
            )
        return queue.pop(0)

    provider = AsyncMock()
    provider.chat_with_tools = AsyncMock(side_effect=_chat_with_tools)
    # Expose the remaining queue so tests can assert how many responses
    # were consumed without re-instrumenting the mock.
    provider._scripted_remaining = queue  # type: ignore[attr-defined]
    return provider


def make_mock_tool_registry(tools: dict[str, ToolHandler]) -> MagicMock:
    """Build a v2-shaped ToolRegistry stub.

    ``tools`` maps a tool name → callable handler. The handler is
    invoked synchronously inside ``execute_tool`` and its return value
    is JSON-serialised if needed (matching the real registry's
    contract). The handler signature is ``(params: dict, task_id: str)``,
    same as ``deskpet.tools.registry.ToolHandler``.

    Returned mock exposes:
      * ``execute_tool(name, params, session_id, task_id="")`` — async
        method returning ``{"ok": bool, "result": str|None, "error": str|None}``.
      * ``schemas()`` — returns OpenAI-format function schemas built
        from the handler names with a minimal ``parameters`` block.
      * ``list_tools()`` — returns the list of registered names
        (handy in assertions).
    """
    handlers: dict[str, ToolHandler] = dict(tools)

    async def _execute_tool(
        name: str,
        params: dict[str, Any],
        session_id: str,
        task_id: str = "",
    ) -> dict[str, Any]:
        handler = handlers.get(name)
        if handler is None:
            return {"ok": False, "result": None, "error": f"unknown tool: {name}"}
        try:
            result = handler(dict(params or {}), task_id)
        except Exception as exc:  # noqa: BLE001 — uniform error envelope
            return {
                "ok": False,
                "result": None,
                "error": f"{type(exc).__name__}: {exc}",
            }
        if not isinstance(result, str):
            try:
                result = json.dumps(result, ensure_ascii=False)
            except (TypeError, ValueError) as exc:
                return {
                    "ok": False,
                    "result": None,
                    "error": f"handler returned non-JSON value: {exc}",
                }
        return {"ok": True, "result": result, "error": None}

    def _schemas(enabled_toolsets: list[str] | None = None) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": f"mock tool {name}",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": True,
                    },
                },
            }
            for name in handlers
        ]

    def _list_tools(source: str | None = None) -> list[str]:
        return list(handlers)

    reg = MagicMock()
    reg.execute_tool = AsyncMock(side_effect=_execute_tool)
    reg.schemas = MagicMock(side_effect=_schemas)
    reg.list_tools = MagicMock(side_effect=_list_tools)
    # Expose the live handler map for tests that want to mutate /
    # inspect it without rebuilding the mock.
    reg._handlers = handlers  # type: ignore[attr-defined]
    return reg


def make_long_message_list(n: int = 30) -> list[dict[str, Any]]:
    """Build a fake chat history with ``n`` messages alternating
    user/assistant. Used by compaction tests that need to feed a loop
    enough history to trigger the compaction trigger.

    The first message is always ``role=system`` so callers can pass the
    output straight to a loop without adjusting indices. The remaining
    messages alternate user (even index) / assistant (odd index).
    """
    if n < 1:
        return []
    out: list[dict[str, Any]] = [
        {"role": "system", "content": "You are a helpful desktop pet."}
    ]
    roles = itertools.cycle(["user", "assistant"])
    for i in range(1, n):
        role = next(roles)
        out.append(
            {
                "role": role,
                "content": f"turn-{i:03d} {role} message body for compaction test",
            }
        )
    return out


def make_tool_call_event(
    name: str, args: dict[str, Any], id: str | None = None
) -> dict[str, Any]:
    """Build a raw tool_call dict — the shape produced by
    ``OpenAICompatibleProvider.chat_with_tools`` and consumed by
    ``tool_use_shim.OpenAICompatibleAgentLLM`` when it constructs a
    ``ToolCall`` pydantic model.

    Shape::

        {"id": "call_<uuid>", "name": "<name>", "arguments": {...}}

    ``id`` is auto-generated when omitted. ``args`` is shallow-copied so
    a caller mutating the source dict after construction doesn't leak
    into the fixture.
    """
    return {
        "id": id if id is not None else f"call_{uuid.uuid4().hex[:8]}",
        "name": name,
        "arguments": dict(args or {}),
    }
