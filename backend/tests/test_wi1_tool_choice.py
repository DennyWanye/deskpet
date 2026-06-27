from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from agent.agent_loop import AgentLoop, ErrorEvent, FinalEvent
from llm.types import ChatResponse, ToolCall
from providers.openai_compatible import OpenAICompatibleProvider


def _raw_response(
    *,
    stop_reason: str = "end_turn",
    content: str = "done",
    value: int = 1,
) -> dict:
    tool_calls = []
    finish_reason = "stop"
    if stop_reason == "tool_use":
        finish_reason = "tool_calls"
        tool_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "noop",
                    "arguments": json.dumps({"value": value}),
                },
            }
        ]
    return {
        "content": content,
        "reasoning_content": "",
        "tool_calls": [
            {"id": "call_1", "name": "noop", "arguments": {"value": value}}
        ] if stop_reason == "tool_use" else [],
        "stop_reason": stop_reason,
        "model": "stub-model",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        "_openai": {
            "id": "cmpl_1",
            "model": "stub-model",
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "message": {
                        "role": "assistant",
                        "content": content,
                        "tool_calls": tool_calls,
                    },
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
    }


class _Tools:
    def schemas(self, enabled_toolsets: Any = None) -> list[dict]:  # noqa: ARG002
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

    def dispatch(self, name: str, args: dict, task_id: str) -> str:  # noqa: ARG002
        return json.dumps({"ok": True}, ensure_ascii=False)


class _ChainProvider:
    model = "stub-model"

    def __init__(self) -> None:
        self.tool_choices: list[str | None] = []
        self.calls = 0

    async def chat_with_tools(
        self,
        messages: list[dict],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> dict:
        self.calls += 1
        self.tool_choices.append(tool_choice)
        stop = "end_turn" if self.calls >= 30 else "tool_use"
        return _raw_response(
            stop_reason=stop,
            content=f"turn {self.calls}",
            value=self.calls,
        )


class _FallbackLLM:
    def __init__(self) -> None:
        self.tool_choices: list[str | None] = []
        self.calls = 0

    async def chat_with_fallback(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.calls += 1
        self.tool_choices.append(kwargs.get("tool_choice"))
        stop = "end_turn" if self.calls >= 30 else "tool_use"
        return ChatResponse(
            content=f"turn {self.calls}",
            stop_reason=stop,
            model="stub-model",
            tool_calls=[
                ToolCall(id="call_1", name="noop", arguments={"value": self.calls})
            ] if stop == "tool_use" else [],
        )


class _StreamingLLM(_FallbackLLM):
    async def chat_with_fallback_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ):
        self.calls += 1
        self.tool_choices.append(kwargs.get("tool_choice"))
        stop = "end_turn" if self.calls >= 30 else "tool_use"
        yield {
            "type": "final",
            "content": f"turn {self.calls}",
            "reasoning_content": "",
            "tool_calls": [
                {"id": "call_1", "name": "noop", "arguments": {"value": self.calls}}
            ] if stop == "tool_use" else [],
            "stop_reason": stop,
            "model": "stub-model",
            "usage": {},
        }


async def _collect(agen):
    out = []
    async for ev in agen:
        out.append(ev)
    return out


@pytest.mark.asyncio
async def test_provider_passes_tool_choice_none() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json=_raw_response(stop_reason="end_turn")["_openai"],
        )

    provider = OpenAICompatibleProvider("https://relay.example/v1", "k", "m")
    provider._test_transport = httpx.MockTransport(handler)

    async for _ in provider.chat_stream_with_tools(
        [{"role": "user", "content": "hi"}],
        tools=_Tools().schemas(),
        tool_choice="none",
    ):
        pass

    assert bodies[0]["tool_choice"] == "none"


@pytest.mark.asyncio
async def test_provider_default_tool_choice_is_auto() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json=_raw_response(stop_reason="end_turn")["_openai"],
        )

    provider = OpenAICompatibleProvider("https://relay.example/v1", "k", "m")
    provider._test_transport = httpx.MockTransport(handler)

    async for _ in provider.chat_stream_with_tools(
        [{"role": "user", "content": "hi"}],
        tools=_Tools().schemas(),
    ):
        pass

    assert bodies[0]["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_tier3_forces_tool_choice_none_in_provider_chain() -> None:
    provider = _ChainProvider()
    loop = AgentLoop(None, _Tools(), max_iterations=31)

    events = await _collect(
        loop.run(
            [{"role": "user", "content": "go"}],
            provider_chain=[provider],
        )
    )

    assert any(isinstance(ev, FinalEvent) for ev in events)
    assert provider.tool_choices[29] == "none"


@pytest.mark.asyncio
async def test_tier3_forces_tool_choice_none_in_stream_path() -> None:
    llm = _StreamingLLM()
    loop = AgentLoop(llm, _Tools(), max_iterations=31)

    events = await _collect(
        loop.run([{"role": "user", "content": "go"}], stream=True)
    )

    assert any(isinstance(ev, FinalEvent) for ev in events)
    assert llm.tool_choices[29] == "none"


@pytest.mark.asyncio
async def test_tier3_forces_tool_choice_none_in_nonstream_path() -> None:
    llm = _FallbackLLM()
    loop = AgentLoop(llm, _Tools(), max_iterations=31)

    events = await _collect(loop.run([{"role": "user", "content": "go"}]))

    assert any(isinstance(ev, FinalEvent) for ev in events)
    assert llm.tool_choices[29] == "none"


@pytest.mark.asyncio
async def test_force_finish_flag_off_never_forces_none() -> None:
    llm = _FallbackLLM()
    loop = AgentLoop(
        llm,
        _Tools(),
        max_iterations=31,
        force_finish_via_tool_choice=False,
    )

    events = await _collect(loop.run([{"role": "user", "content": "go"}]))

    assert any(isinstance(ev, FinalEvent) for ev in events)
    assert "none" not in llm.tool_choices


@pytest.mark.asyncio
async def test_tier3_suppresses_completion_nudge() -> None:
    llm = _FallbackLLM()
    probe_calls = 0

    async def completion_probe(session_id: str) -> list[dict]:  # noqa: ARG001
        nonlocal probe_calls
        probe_calls += 1
        return [{"content": "unfinished", "status": "pending"}]

    loop = AgentLoop(
        llm,
        _Tools(),
        max_iterations=31,
        completion_probe=completion_probe,
        max_completion_nudges=2,
    )

    events = await _collect(loop.run([{"role": "user", "content": "go"}]))

    assert any(isinstance(ev, FinalEvent) for ev in events)
    assert probe_calls == 0


class _AlwaysEndTurnLLM:
    """Every turn returns plain end_turn text → always reaches the verify gate."""

    def __init__(self) -> None:
        self.tool_choices: list[str | None] = []
        self.calls = 0

    async def chat_with_fallback(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.calls += 1
        self.tool_choices.append(kwargs.get("tool_choice"))
        return ChatResponse(
            content=f"claim turn {self.calls}",
            stop_reason="end_turn",
            model="stub-model",
            tool_calls=[],
        )


class _FakeClaim:
    def __init__(self, raw_text: str = "I created report.pptx") -> None:
        self.raw_text = raw_text
        self.reason = "no_receipt"
        self.pattern_id = "p1"


class _FailVerifyOutcome:
    passed = False
    unmatched_claims = [_FakeClaim()]
    goal_alignment = None


class _FailVerifyGate:
    """Verify gate that never matches → forces exhaustion path (B5)."""

    mode = "strict"

    def check(self, *, assistant_text: str, ledger: Any, goal_text: Any = None):  # noqa: ARG002
        return _FailVerifyOutcome()

    def build_rebound_message(self, unmatched: Any) -> str:  # noqa: ARG002
        return "rebound: please prove your claims"

    async def consult_ephemeral_subagent(
        self, *, ledger: Any, failed_claims: Any, assistant_text: Any  # noqa: ARG002
    ) -> bool:
        return False


@pytest.mark.asyncio
async def test_verify_exhausted_grants_final_text_turn() -> None:
    """B5/§17.6: verify exhaustion → one forced tool_choice=none summary turn →
    terminal ErrorEvent(verify_exhausted) carrying the model summary in detail."""
    llm = _AlwaysEndTurnLLM()
    loop = AgentLoop(
        llm,
        _Tools(),
        max_iterations=10,
        verify_gate=_FailVerifyGate(),
        max_verify_nudges=2,
    )

    events = await _collect(loop.run([{"role": "user", "content": "go"}]))

    errors = [ev for ev in events if isinstance(ev, ErrorEvent)]
    assert errors, "verify exhaustion must surface an ErrorEvent (graceful degrade)"
    assert errors[-1].reason == "verify_exhausted"
    # the failure was surfaced with the model's final summary text, not hidden
    assert errors[-1].detail
    # the granted final turn used tool_choice="none" (protocol-level hard stop)
    assert "none" in llm.tool_choices


@pytest.mark.asyncio
async def test_verify_exhausted_flag_off_hard_exits_without_final_turn() -> None:
    """BC: with force_finish flag off, exhaustion hard-exits (no forced none turn)."""
    llm = _AlwaysEndTurnLLM()
    loop = AgentLoop(
        llm,
        _Tools(),
        max_iterations=10,
        verify_gate=_FailVerifyGate(),
        max_verify_nudges=2,
        force_finish_via_tool_choice=False,
    )

    events = await _collect(loop.run([{"role": "user", "content": "go"}]))

    errors = [ev for ev in events if isinstance(ev, ErrorEvent)]
    assert errors and errors[-1].reason == "verify_exhausted"
    assert "none" not in llm.tool_choices
