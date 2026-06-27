# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P5-S2 Hook A: completion guard in agent_loop.

When the LLM tries to finalize (stop_reason ≠ tool_use) but the session
still has incomplete todos, the loop must rebound with a system message
instead of finalizing — up to ``max_completion_nudges`` times. Then it
gives up and finalizes anyway so we can't infinite-loop on a stubborn
LLM.
"""
from __future__ import annotations

from typing import Any

import pytest

from agent.agent_loop import AgentLoop, FinalEvent
from llm.types import ChatResponse, ChatUsage


# ─────────────── stubs ───────────────


class _ScriptedLLM:
    """Replays a fixed sequence of ChatResponse objects in order. Each
    call to ``chat_with_fallback`` consumes the next entry. Records all
    messages it received so tests can assert what was sent."""

    def __init__(self, responses: list[ChatResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []

    async def chat_with_fallback(
        self,
        messages: list[dict[str, Any]],
        **_: Any,
    ) -> ChatResponse:
        # Take a snapshot so caller mutations don't affect later assertions
        self.calls.append([dict(m) for m in messages])
        if not self._responses:
            raise AssertionError("ScriptedLLM exhausted")
        return self._responses.pop(0)


class _NoopToolRegistry:
    def schemas(self, enabled_toolsets: Any = None) -> list[dict]:  # noqa: ARG002
        return []

    async def dispatch(self, name: str, args: dict, task_id: str) -> str:  # noqa: ARG002
        return "{}"


def _resp(content: str = "ok", stop: str = "end_turn") -> ChatResponse:
    return ChatResponse(
        content=content,
        tool_calls=[],
        stop_reason=stop,
        model="stub",
        usage=ChatUsage(input_tokens=1, output_tokens=1),
    )


# ─────────────── tests ───────────────


@pytest.mark.asyncio
async def test_no_probe_means_no_rebound() -> None:
    """Default behaviour (no completion_probe) is unchanged: end_turn
    finalizes immediately even with todos that the loop can't see."""
    llm = _ScriptedLLM([_resp("done")])
    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=_NoopToolRegistry(),
        max_iterations=5,
    )
    events = [ev async for ev in loop.run([{"role": "user", "content": "hi"}], session_id="s1")]
    assert any(isinstance(e, FinalEvent) for e in events)
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_probe_rebounds_when_todos_pending() -> None:
    """LLM says end_turn, probe reports incomplete todos → loop appends
    a system nudge and re-iterates instead of finalizing."""
    llm = _ScriptedLLM([
        _resp("I'm done.", stop="end_turn"),         # turn 1: tries to finalize
        _resp("Done for real this time.", stop="end_turn"),  # turn 2: finalizes after rebound
    ])

    probe_calls: list[str] = []
    incomplete_payload = [
        {"content": "wire VPN tunnel", "status": "pending"},
        {"content": "verify ping", "status": "in_progress"},
    ]
    next_return = [incomplete_payload, []]  # second probe says "all done now"

    async def _probe(sid: str) -> list[dict]:
        probe_calls.append(sid)
        return next_return.pop(0)

    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=_NoopToolRegistry(),
        max_iterations=5,
        completion_probe=_probe,
        max_completion_nudges=2,
    )
    events = [ev async for ev in loop.run([{"role": "user", "content": "hi"}], session_id="sess-X")]
    finals = [e for e in events if isinstance(e, FinalEvent)]
    assert len(finals) == 1
    # 2 LLM calls: original + 1 rebound
    assert len(llm.calls) == 2
    # Probe was hit twice (once per "tries to end_turn")
    assert probe_calls == ["sess-X", "sess-X"]
    # Second LLM call's prompt must contain the rebound system message
    second_msgs = llm.calls[1]
    rebound = next((m for m in second_msgs if m["role"] == "system" and "todos" in m["content"]), None)
    assert rebound is not None, f"expected rebound system msg in second call: {second_msgs!r}"
    assert "wire VPN tunnel" in rebound["content"]
    assert "2 项未完成" in rebound["content"]


@pytest.mark.asyncio
async def test_max_nudges_caps_rebound() -> None:
    """If LLM keeps trying to finalize and todos stay pending, the loop
    rebounds at most ``max_completion_nudges`` times then gives up and
    actually emits FinalEvent."""
    llm = _ScriptedLLM([_resp(f"done #{i}", stop="end_turn") for i in range(10)])

    async def _probe(_sid: str) -> list[dict]:
        return [{"content": "still pending", "status": "pending"}]

    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=_NoopToolRegistry(),
        max_iterations=10,
        completion_probe=_probe,
        max_completion_nudges=2,
    )
    events = [ev async for ev in loop.run([{"role": "user", "content": "hi"}], session_id="s")]
    finals = [e for e in events if isinstance(e, FinalEvent)]
    assert len(finals) == 1
    # 1 original + 2 rebounds = 3 LLM calls before giving up
    assert len(llm.calls) == 3


@pytest.mark.asyncio
async def test_probe_exception_falls_through_to_finalize() -> None:
    """Probe raising an exception must not crash the loop — degrade
    gracefully: log + finalize as if no probe was set."""
    llm = _ScriptedLLM([_resp("done")])

    async def _bad_probe(_sid: str) -> list[dict]:
        raise RuntimeError("DB exploded")

    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=_NoopToolRegistry(),
        max_iterations=5,
        completion_probe=_bad_probe,
        max_completion_nudges=2,
    )
    events = [ev async for ev in loop.run([{"role": "user", "content": "hi"}], session_id="s")]
    assert any(isinstance(e, FinalEvent) for e in events)
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_empty_incomplete_means_no_rebound() -> None:
    """Probe returns [] (all done) → loop finalizes normally, no rebound."""
    llm = _ScriptedLLM([_resp("done")])

    async def _probe(_sid: str) -> list[dict]:
        return []

    loop = AgentLoop(
        llm_registry=llm,
        tool_registry=_NoopToolRegistry(),
        max_iterations=5,
        completion_probe=_probe,
        max_completion_nudges=2,
    )
    events = [ev async for ev in loop.run([{"role": "user", "content": "hi"}], session_id="s")]
    assert any(isinstance(e, FinalEvent) for e in events)
    assert len(llm.calls) == 1
