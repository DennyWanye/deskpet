# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P5-S2 Phase 3.1: AgentLoop walks a provider chain.

When AgentLoop.run() is invoked with a ``provider_chain`` parameter,
each LLM call inside the loop tries providers in order, falling back
on transient errors only. Permanent errors (P5-S2 Phase 2 PermanentTool
signals) short-circuit so we don't waste a fallback request on a
deterministic bug.

Reference: openspec/changes/multi-provider-management/specs/agent-loop/spec.md
"""
from __future__ import annotations

from typing import Any

import pytest

from agent.agent_loop import (
    AgentLoop,
    AssistantMessageEvent,
    ErrorEvent,
    FinalEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from llm.errors import LLMProviderError


# ─────────────────────── stub providers / tools ───────────────────────


class _FakeProvider:
    """Mimics ``OpenAICompatibleProvider.chat_with_tools``.

    Each instance has an ``id`` for diagnostic asserts, and a
    ``responses`` queue of dicts (matching the raw shape returned by
    OpenAICompatibleProvider). Each call pops one off. If ``responses``
    is empty when called, raises AssertionError (means the chain walked
    past where the test expected).

    If ``raise_exc`` is set instead of ``responses``, calling
    chat_with_tools raises that exception (used to model transient
    failures).
    """

    def __init__(
        self,
        provider_id: str,
        *,
        model: str = "stub-model",
        responses: list[dict] | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self.id = provider_id
        self.model = model
        self.responses = list(responses or [])
        self.raise_exc = raise_exc
        self.call_count = 0
        # Capture last model arg passed (for preferred_model override
        # assertions in resolution tests — kept here too for symmetry).
        self.last_model_used: str | None = None

    async def chat_with_tools(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        max_tokens: int = 2048,
        temperature: float | None = None,
        response_format: dict | None = None,
        model: str | None = None,
    ) -> dict:
        self.call_count += 1
        # Provider may receive an override model; record it.
        self.last_model_used = model or self.model
        if self.raise_exc is not None:
            # Always raise — simulates a permanently broken provider.
            raise self.raise_exc
        if not self.responses:
            raise AssertionError(
                f"_FakeProvider({self.id}) exhausted at call_count="
                f"{self.call_count}; test expected fewer calls"
            )
        return self.responses.pop(0)


class _NoopTools:
    """Trivial tool registry: no tools, never dispatched."""

    def schemas(self, enabled_toolsets: Any = None) -> list[dict]:  # noqa: ARG002
        return []

    def dispatch(self, name: str, args: dict, task_id: str) -> str:  # noqa: ARG002
        raise AssertionError("no tools should be dispatched in these tests")


def _ok_response(content: str = "done", provider_id: str = "?") -> dict:
    """Final assistant response (no tool_calls) that ends the loop."""
    return {
        "content": content,
        "reasoning_content": "",
        "tool_calls": [],
        "stop_reason": "end_turn",
        "model": f"model-from-{provider_id}",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


def _malformed_args_response(provider_id: str = "?") -> dict:
    """A response with a tool_call whose JSON args failed to parse.

    AgentLoop's dispatch layer treats this as a P5-S2 permanent
    tool error and short-circuits — we use it to assert no fallback
    happens when the FIRST provider returns a structurally broken
    tool_call.
    """
    return {
        "content": "",
        "reasoning_content": "",
        "tool_calls": [
            {
                "id": "call_bad",
                "name": "write_file",
                "arguments": {},
                "_args_raw": '{"path": "fo',  # truncated
                "_args_parse_error": "Unterminated string at column 13",
            }
        ],
        "stop_reason": "tool_use",
        "model": f"model-from-{provider_id}",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


async def _collect(agen):
    """Drain an async generator into a list."""
    out = []
    async for ev in agen:
        out.append(ev)
    return out


# ────────────────────── tests ──────────────────────


@pytest.mark.asyncio
async def test_first_provider_succeeds_others_unused() -> None:
    """3.1: chain[A, B, C], A returns valid response → B/C never called."""
    a = _FakeProvider("A", responses=[_ok_response("hi", "A")])
    b = _FakeProvider("B", responses=[_ok_response("nope", "B")])
    c = _FakeProvider("C", responses=[_ok_response("nope", "C")])

    loop = AgentLoop(llm_registry=None, tool_registry=_NoopTools())
    events = await _collect(
        loop.run(
            [{"role": "user", "content": "hi"}],
            session_id="t1",
            provider_chain=[a, b, c],
        )
    )

    # A called exactly once; B + C never touched.
    assert a.call_count == 1
    assert b.call_count == 0
    assert c.call_count == 0

    # Loop produced one assistant_message + one final, no errors.
    asst = [e for e in events if isinstance(e, AssistantMessageEvent)]
    finals = [e for e in events if isinstance(e, FinalEvent)]
    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(asst) == 1, events
    assert len(finals) == 1, events
    assert errors == []
    assert finals[0].content == "hi"


@pytest.mark.asyncio
async def test_transient_error_falls_to_next_provider() -> None:
    """3.2: A raises LLMProviderError → fallback to B; B succeeds."""
    a = _FakeProvider(
        "A",
        raise_exc=LLMProviderError("ConnectTimeout: handshake too slow"),
    )
    b = _FakeProvider("B", responses=[_ok_response("rescued", "B")])
    c = _FakeProvider("C", responses=[_ok_response("nope", "C")])

    loop = AgentLoop(llm_registry=None, tool_registry=_NoopTools())
    events = await _collect(
        loop.run(
            [{"role": "user", "content": "go"}],
            session_id="t2",
            provider_chain=[a, b, c],
        )
    )

    assert a.call_count == 1
    assert b.call_count == 1
    assert c.call_count == 0  # B answered, never reached C

    # Final from B's content.
    finals = [e for e in events if isinstance(e, FinalEvent)]
    assert len(finals) == 1
    assert finals[0].content == "rescued"

    # No ErrorEvent in the final output.
    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert errors == []


@pytest.mark.asyncio
async def test_permanent_error_does_NOT_fall_to_next_provider() -> None:
    """3.3: A returns tool_call.args_parse_error → loop does NOT try B.

    args_parse_error is a SUCCESSFUL provider response (no exception)
    that the agent loop's dispatch layer treats as a Phase-2 permanent
    error. Chain walking only fires on raised LLMProviderError, so B
    should never be touched.
    """
    a = _FakeProvider("A", responses=[_malformed_args_response("A")])
    b = _FakeProvider("B", responses=[_ok_response("never-called", "B")])

    loop = AgentLoop(
        llm_registry=None,
        tool_registry=_NoopTools(),
        max_iterations=3,
    )
    events = await _collect(
        loop.run(
            [{"role": "user", "content": "x"}],
            session_id="t3",
            provider_chain=[a, b],
        )
    )

    # A called once; B NEVER called (short-circuit).
    assert a.call_count == 1
    assert b.call_count == 0


@pytest.mark.asyncio
async def test_all_providers_fail_emits_all_providers_failed_error_event() -> None:
    """3.4: A, B, C all raise transient → ErrorEvent(all_providers_failed)."""
    a = _FakeProvider("A", raise_exc=LLMProviderError("ConnectTimeout"))
    b = _FakeProvider("B", raise_exc=LLMProviderError("ReadTimeout"))
    c = _FakeProvider(
        "C",
        raise_exc=LLMProviderError("RemoteProtocolError: server disconnected"),
    )

    loop = AgentLoop(llm_registry=None, tool_registry=_NoopTools())
    events = await _collect(
        loop.run(
            [{"role": "user", "content": "x"}],
            session_id="t4",
            provider_chain=[a, b, c],
        )
    )

    assert a.call_count == 1
    assert b.call_count == 1
    assert c.call_count == 1

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(errors) == 1, events
    err = errors[0]
    assert err.reason == "all_providers_failed"
    assert "tried 3" in err.detail
    # Last error text included so AutoResume can diagnose.
    assert "RemoteProtocolError" in err.detail


@pytest.mark.asyncio
async def test_empty_chain_emits_no_provider_configured_immediately() -> None:
    """3.5: chain=[] → ErrorEvent(no_provider_configured) before any call."""
    loop = AgentLoop(llm_registry=None, tool_registry=_NoopTools())
    events = await _collect(
        loop.run(
            [{"role": "user", "content": "x"}],
            session_id="t5",
            provider_chain=[],
        )
    )

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(errors) == 1, events
    err = errors[0]
    assert err.reason == "no_provider_configured"
    # User-facing Chinese guidance (matches spec text).
    assert "未配置任何 LLM provider" in err.detail
    assert "设置" in err.detail and "LLM Providers" in err.detail


@pytest.mark.asyncio
async def test_provider_chain_fallback_ws_event_emitted() -> None:
    """3.6: on fallback, loop yields a provider_chain_fallback event.

    main.py forwards this to the ws as
    ``{type: "provider_chain_fallback", session_id, from, to, reason}``.
    """
    a = _FakeProvider("A", raise_exc=LLMProviderError("ConnectTimeout: dns"))
    b = _FakeProvider("B", responses=[_ok_response("ok-from-b", "B")])

    loop = AgentLoop(llm_registry=None, tool_registry=_NoopTools())
    events = await _collect(
        loop.run(
            [{"role": "user", "content": "go"}],
            session_id="sid-99",
            provider_chain=[a, b],
        )
    )

    # Look for the fallback event in the stream — duck-typed (could be
    # a dedicated event class or a dict-like, depending on
    # implementation). We accept any AgentEvent-shaped object with
    # type == "provider_chain_fallback".
    fb_events = [
        e for e in events
        if getattr(e, "type", None) == "provider_chain_fallback"
    ]
    assert len(fb_events) == 1, [getattr(e, "type", None) for e in events]
    fb = fb_events[0]
    # session_id from agent loop run() arg
    assert getattr(fb, "session_id", None) == "sid-99"
    # 'from' is a Python keyword — use from_ on the event class.
    from_id = getattr(fb, "from_", None) or getattr(fb, "from_provider", None)
    to_id = getattr(fb, "to", None) or getattr(fb, "to_provider", None)
    assert from_id == "A", fb
    assert to_id == "B", fb
    # reason captures the LLMProviderError message tail.
    reason = getattr(fb, "reason", "") or ""
    assert "ConnectTimeout" in reason or "Timeout" in reason


# ─────────────────────── backwards-compat test (3.14) ───────────────────────


class _LegacyLLM:
    """Mimics the legacy LLMRegistry shim — has chat_with_fallback only."""

    def __init__(self, response: dict) -> None:
        self._response = response
        self.call_count = 0

    async def chat_with_fallback(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> Any:
        from agent.tool_use_shim import _raw_to_response
        self.call_count += 1
        return _raw_to_response(self._response)


@pytest.mark.asyncio
async def test_legacy_single_provider_callers_still_work() -> None:
    """3.14: AgentLoop without provider_chain still uses llm_registry.

    All pre-Phase-3 tests pass a single shim that exposes
    chat_with_fallback. That code path MUST keep working unchanged
    when ``provider_chain`` is omitted.
    """
    llm = _LegacyLLM(_ok_response("legacy-path", "legacy"))
    loop = AgentLoop(llm_registry=llm, tool_registry=_NoopTools())
    events = await _collect(
        loop.run([{"role": "user", "content": "x"}], session_id="legacy-1")
    )

    assert llm.call_count == 1
    finals = [e for e in events if isinstance(e, FinalEvent)]
    assert len(finals) == 1
    assert finals[0].content == "legacy-path"
    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert errors == []
