"""DeskPet agent loop (P4-S6 §11 skeleton).

One pass = one LLM turn + optional tool dispatch round. Loop until
`stop_reason != "tool_use"` or `max_iterations` hit, whichever comes
first.

Yielded events (AgentEvent) — caller decides what to do with each:

    assistant_message   assistant text chunk (content from the model)
    tool_call           a tool the model asked to invoke
    tool_result         output of a dispatched tool (JSON string)
    final               last turn; final response + aggregated stats
    error               terminal failure (budget exceeded, max_iter, LLM error)

The loop does NOT talk to the wire itself. Callers wire:
    llm_registry: must expose `async chat_with_fallback(...)` → ChatResponse
    tool_registry: must expose
        - `schemas(enabled_toolsets=None)` → list[dict]   (OpenAI format)
        - `async dispatch(name, args, task_id)` → str     (JSON string; see §5 contract)

Tool dispatch is concurrent (asyncio.gather) when the model requests
multiple tool_calls in one turn (spec §11.9). Each tool result is fed
back as a `role=tool` message and the loop iterates.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional, Protocol, Union

from agent.task_id import new_task_id
from llm.budget import DailyBudget
from llm.errors import LLMBudgetExceededError, LLMProviderError
from llm.types import ChatResponse, ToolCall

logger = logging.getLogger("deskpet.agent.loop")


# ───────────────────── event dataclasses ─────────────────────


@dataclass
class AgentEvent:
    """Base event emitted by the agent loop.

    All fields default so subclasses can freely add non-default fields
    without hitting python's "non-default follows default" check. Each
    subclass overrides `type` in __post_init__ via a class-level default.
    """

    type: str = ""
    task_id: str = ""
    iteration: int = 0


@dataclass
class AssistantMessageEvent(AgentEvent):
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""
    model: str = ""

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "assistant_message"


@dataclass
class ToolCallEvent(AgentEvent):
    tool_call: Optional[ToolCall] = None

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "tool_call"


@dataclass
class ToolResultEvent(AgentEvent):
    tool_call_id: str = ""
    tool_name: str = ""
    result: str = ""  # JSON string

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "tool_result"


@dataclass
class FinalEvent(AgentEvent):
    content: str = ""
    stop_reason: str = "end_turn"
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_write_tokens: int = 0

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "final"


@dataclass
class ErrorEvent(AgentEvent):
    reason: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "error"


# ───────────────────── protocols for caller dependencies ─────────────────────


class _LLMRegistryProto(Protocol):
    async def chat_with_fallback(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> ChatResponse: ...


class _ToolRegistryProto(Protocol):
    def schemas(self, enabled_toolsets: Optional[list[str]] = None) -> list[dict[str, Any]]: ...
    def dispatch(self, name: str, args: dict[str, Any], task_id: str) -> Any: ...


# ───────────────────── agent loop ─────────────────────


class AgentLoop:
    """ReAct-style driver around LLMRegistry + ToolRegistry."""

    def __init__(
        self,
        llm_registry: _LLMRegistryProto,
        tool_registry: _ToolRegistryProto,
        *,
        max_iterations: int = 20,
        budget_checker: Optional[DailyBudget] = None,
        default_model: Optional[str] = None,
    ) -> None:
        self.llm = llm_registry
        self.tools = tool_registry
        self.max_iterations = max_iterations
        self.budget_checker = budget_checker
        self.default_model = default_model
        # P4-S20: detect v2 ToolRegistry (has async execute_tool with
        # built-in permission gating). When present, dispatch routes
        # through it so user-permission popups work end-to-end. Legacy
        # registries (only `dispatch`) keep working unchanged.
        self._supports_execute_tool = callable(getattr(tool_registry, "execute_tool", None))

    async def run(
        self,
        messages: list[dict[str, Any]],
        *,
        task_id: Optional[str] = None,
        tools_filter: Optional[list[str]] = None,
        model: Optional[str] = None,
        session_id: str = "default",
        **llm_kwargs: Any,
    ) -> AsyncIterator[AgentEvent]:
        """Drive the ReAct loop. See module docstring for event contract."""
        tid = task_id or new_task_id()
        working_messages: list[dict[str, Any]] = list(messages)
        tool_schemas = self.tools.schemas(enabled_toolsets=tools_filter)

        totals = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        use_model = model or self.default_model

        for iteration in range(1, self.max_iterations + 1):
            # Budget gate BEFORE the call — can't take back tokens after the fact.
            if self.budget_checker is not None and not self.budget_checker.check_allowed():
                yield ErrorEvent(
                    type="error",
                    task_id=tid,
                    iteration=iteration,
                    reason="budget_exceeded",
                    detail=f"daily budget cap reached (${self.budget_checker.cap_usd:.2f})",
                )
                return

            try:
                response = await self.llm.chat_with_fallback(
                    working_messages,
                    tools=tool_schemas or None,
                    model=use_model,
                    **llm_kwargs,
                )
            except LLMBudgetExceededError as exc:
                yield ErrorEvent(
                    type="error",
                    task_id=tid,
                    iteration=iteration,
                    reason="budget_exceeded",
                    detail=str(exc),
                )
                return
            except LLMProviderError as exc:
                yield ErrorEvent(
                    type="error",
                    task_id=tid,
                    iteration=iteration,
                    reason="llm_error",
                    detail=str(exc),
                )
                return

            totals["input"] += response.usage.input_tokens
            totals["output"] += response.usage.output_tokens
            totals["cache_read"] += response.usage.cache_read_tokens
            totals["cache_write"] += response.usage.cache_write_tokens

            yield AssistantMessageEvent(
                type="assistant_message",
                task_id=tid,
                iteration=iteration,
                content=response.content,
                tool_calls=list(response.tool_calls),
                stop_reason=response.stop_reason,
                model=response.model,
            )

            # End of conversation — emit final and stop.
            if response.stop_reason != "tool_use" or not response.tool_calls:
                yield FinalEvent(
                    type="final",
                    task_id=tid,
                    iteration=iteration,
                    content=response.content,
                    stop_reason=response.stop_reason or "end_turn",
                    total_input_tokens=totals["input"],
                    total_output_tokens=totals["output"],
                    total_cache_read_tokens=totals["cache_read"],
                    total_cache_write_tokens=totals["cache_write"],
                )
                return

            # Append assistant turn with tool_calls so next LLM turn sees it.
            # OpenAI requires arguments to be a JSON STRING, not a dict.
            # Some adapters (anthropic_adapter) want the dict form. We
            # encode here as a string since the OpenAI-compat path is
            # the most common downstream.
            import json as _json_at
            working_messages.append(
                {
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": _json_at.dumps(
                                    tc.arguments, ensure_ascii=False
                                ),
                            },
                        }
                        for tc in response.tool_calls
                    ],
                }
            )

            # Dispatch all tools concurrently (spec §11.9).
            tool_coros = []
            call_order: list[ToolCall] = []
            for tc in response.tool_calls:
                yield ToolCallEvent(
                    type="tool_call",
                    task_id=tid,
                    iteration=iteration,
                    tool_call=tc,
                )
                tool_coros.append(self._dispatch_tool(tc, tid, session_id))
                call_order.append(tc)

            results = await asyncio.gather(*tool_coros, return_exceptions=True)

            for tc, result in zip(call_order, results):
                if isinstance(result, BaseException):
                    # _dispatch_tool already normalizes most exceptions; this
                    # is a defense-in-depth catch for anything that slipped past.
                    import json as _json  # local import: rarely used path

                    result_str = _json.dumps(
                        {"error": f"{type(result).__name__}: {result}", "retriable": False},
                        ensure_ascii=False,
                    )
                else:
                    result_str = result
                yield ToolResultEvent(
                    type="tool_result",
                    task_id=tid,
                    iteration=iteration,
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    result=result_str,
                )
                working_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "content": result_str,
                    }
                )

        # Hit max_iterations — spec §11.4 says emit warning + final.
        logger.warning("agent loop task %s hit max_iterations=%d", tid, self.max_iterations)
        yield ErrorEvent(
            type="error",
            task_id=tid,
            iteration=self.max_iterations,
            reason="max_iterations",
            detail=f"exceeded {self.max_iterations} iterations without terminal stop_reason",
        )

    async def _dispatch_tool(
        self, tc: ToolCall, task_id: str, session_id: str = "default"
    ) -> str:
        """Call tool_registry to run a tool with graceful error shaping.

        P4-S20 routing:
          - v2 registry (has ``execute_tool``) → permission-gated async
            path. Returns ``{"ok", "result", "error"}`` envelope as JSON
            string so the LLM tool_result turn sees structured outcome
            (and can apologize on permission denial instead of
            hallucinating success).
          - legacy registry → ``dispatch()`` per spec §5 error contract.
        """
        import json as _json
        try:
            if self._supports_execute_tool:
                envelope = await self.tools.execute_tool(  # type: ignore[attr-defined]
                    tc.name, tc.arguments, session_id, task_id
                )
                # Pass through envelope to LLM as JSON. Tools that
                # succeeded already encoded their domain result inside
                # `result` (typically a JSON string); we don't
                # double-encode.
                return _json.dumps(envelope, ensure_ascii=False)
            result = self.tools.dispatch(tc.name, tc.arguments, task_id)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:  # noqa: BLE001
            return _json.dumps(
                {
                    "error": f"{type(exc).__name__}: {exc}",
                    "retriable": False,
                },
                ensure_ascii=False,
            )
        if isinstance(result, (dict, list)):
            return _json.dumps(result, ensure_ascii=False)
        return str(result)


# Runtime AgentEvent union type for callers that want isinstance checks.
AgentEventUnion = Union[
    AssistantMessageEvent,
    ToolCallEvent,
    ToolResultEvent,
    FinalEvent,
    ErrorEvent,
]
