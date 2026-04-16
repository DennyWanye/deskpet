"""Tests for ToolUsingAgent — text-protocol tool routing layer."""
from __future__ import annotations

from typing import AsyncIterator

import pytest

from agent.providers.base import AgentProvider
from agent.providers.tool_using import ToolUsingAgent
from tools.base import ToolSpec
from tools.registry import ToolRegistry


class StubAgent:
    """Replays a fixed token sequence; records the messages it was called with."""

    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.last_messages: list[dict[str, str]] | None = None
        self.last_session_id: str | None = None

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        session_id: str = "default",
    ) -> AsyncIterator[str]:
        self.last_messages = messages
        self.last_session_id = session_id
        for t in self.tokens:
            yield t


class FakeTool:
    def __init__(self, name: str, result: str = "ok") -> None:
        self.spec = ToolSpec(name=name, description=f"fake tool {name}")
        self._result = result
        self.call_count = 0

    async def invoke(self, **kwargs: object) -> str:
        self.call_count += 1
        return self._result


@pytest.mark.asyncio
async def test_no_tool_tag_passes_through():
    base = StubAgent(["hello ", "world"])
    reg = ToolRegistry()
    agent = ToolUsingAgent(base=base, registry=reg)

    collected = []
    async for tok in agent.chat_stream([{"role": "user", "content": "hi"}]):
        collected.append(tok)

    assert "".join(collected) == "hello world"


@pytest.mark.asyncio
async def test_tool_tag_triggers_invoke_and_appends_result():
    base = StubAgent(["Let me check <tool>", "get_time", "</tool>"])
    reg = ToolRegistry()
    fake = FakeTool("get_time", result="2026-04-14T12:00:00")
    reg.register(fake)

    agent = ToolUsingAgent(base=base, registry=reg, inject_system_prompt=False)

    full = ""
    async for tok in agent.chat_stream([{"role": "user", "content": "time?"}]):
        full += tok

    assert fake.call_count == 1
    assert "<tool>get_time</tool>" in full  # original text still there
    assert "[tool:get_time]" in full
    assert "2026-04-14T12:00:00" in full


@pytest.mark.asyncio
async def test_unknown_tool_emits_not_found():
    base = StubAgent(["<tool>bogus</tool>"])
    reg = ToolRegistry()
    agent = ToolUsingAgent(base=base, registry=reg, inject_system_prompt=False)

    full = ""
    async for tok in agent.chat_stream([{"role": "user", "content": "x"}]):
        full += tok

    assert "[tool not found: bogus]" in full


@pytest.mark.asyncio
async def test_tool_error_is_caught_and_reported():
    class BrokenTool:
        spec = ToolSpec(name="broken", description="always fails")

        async def invoke(self, **kwargs: object) -> str:
            raise RuntimeError("boom")

    base = StubAgent(["<tool>broken</tool>"])
    reg = ToolRegistry()
    reg.register(BrokenTool())
    agent = ToolUsingAgent(base=base, registry=reg, inject_system_prompt=False)

    full = ""
    async for tok in agent.chat_stream([{"role": "user", "content": "x"}]):
        full += tok

    assert "[tool error: broken: boom]" in full


@pytest.mark.asyncio
async def test_system_prompt_injected_when_tools_exist():
    base = StubAgent(["reply"])
    reg = ToolRegistry()
    reg.register(FakeTool("get_time"))
    agent = ToolUsingAgent(base=base, registry=reg, inject_system_prompt=True)

    async for _ in agent.chat_stream([{"role": "user", "content": "hi"}]):
        pass

    assert base.last_messages is not None
    assert base.last_messages[0]["role"] == "system"
    assert "get_time" in base.last_messages[0]["content"]
    assert base.last_messages[1]["role"] == "user"


@pytest.mark.asyncio
async def test_no_system_prompt_when_no_tools():
    """No tools registered → no system prompt injected."""
    base = StubAgent(["reply"])
    reg = ToolRegistry()  # empty — no tools
    agent = ToolUsingAgent(base=base, registry=reg, inject_system_prompt=True)

    async for _ in agent.chat_stream([{"role": "user", "content": "hi"}]):
        pass

    # prompt_hint is empty → no system message prepended
    assert base.last_messages == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_session_id_forwarded_to_base():
    base = StubAgent(["x"])
    reg = ToolRegistry()
    agent = ToolUsingAgent(base=base, registry=reg, inject_system_prompt=False)

    async for _ in agent.chat_stream(
        [{"role": "user", "content": "hi"}], session_id="user-42"
    ):
        pass

    assert base.last_session_id == "user-42"


def test_tool_using_agent_satisfies_agent_provider_protocol():
    base = StubAgent([])
    reg = ToolRegistry()
    agent = ToolUsingAgent(base=base, registry=reg)
    assert isinstance(agent, AgentProvider)


@pytest.mark.asyncio
async def test_existing_system_prompt_preserved():
    """If caller already supplied a system message, don't stomp it."""
    base = StubAgent(["reply"])
    reg = ToolRegistry()
    reg.register(FakeTool("get_time"))
    agent = ToolUsingAgent(base=base, registry=reg, inject_system_prompt=True)

    caller_msgs = [
        {"role": "system", "content": "You are a pirate"},
        {"role": "user", "content": "hi"},
    ]
    async for _ in agent.chat_stream(caller_msgs):
        pass

    assert base.last_messages == caller_msgs  # untouched
