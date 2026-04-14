"""Unit tests for AgentProvider abstraction (Slice 0).

Covers:
- AgentProvider Protocol runtime_checkable
- SimpleLLMAgent correctly proxies LLMProvider.chat_stream
- session_id parameter accepted but currently ignored
- ServiceContext.agent_engine slot accepts AgentProvider instances
"""
from __future__ import annotations

import pytest

from agent.providers.base import AgentProvider
from agent.providers.simple_llm import SimpleLLMAgent
from context import ServiceContext


class FakeLLM:
    """Minimal stub matching LLMProvider Protocol."""

    def __init__(self, tokens: list[str] | None = None) -> None:
        self.tokens = tokens or ["hello", " ", "world"]
        self.last_messages: list[dict[str, str]] | None = None

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ):
        self.last_messages = messages
        for tok in self.tokens:
            yield tok

    async def health_check(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_simple_llm_agent_proxies_tokens():
    llm = FakeLLM(tokens=["a", "b", "c"])
    agent = SimpleLLMAgent(llm)

    collected: list[str] = []
    async for tok in agent.chat_stream([{"role": "user", "content": "hi"}]):
        collected.append(tok)

    assert collected == ["a", "b", "c"]
    assert llm.last_messages == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_simple_llm_agent_accepts_session_id():
    """session_id is accepted but currently forwarded to nothing — S2 will use it."""
    llm = FakeLLM(tokens=["x"])
    agent = SimpleLLMAgent(llm)

    collected: list[str] = []
    async for tok in agent.chat_stream(
        [{"role": "user", "content": "hi"}],
        session_id="user-42",
    ):
        collected.append(tok)

    assert collected == ["x"]


def test_simple_llm_agent_satisfies_agent_provider_protocol():
    llm = FakeLLM()
    agent = SimpleLLMAgent(llm)
    assert isinstance(agent, AgentProvider)


def test_service_context_accepts_agent_engine():
    ctx = ServiceContext()
    assert ctx.agent_engine is None

    agent = SimpleLLMAgent(FakeLLM())
    ctx.register("agent_engine", agent)
    assert ctx.agent_engine is agent
