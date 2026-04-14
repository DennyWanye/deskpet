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


# --- S2 memory injection tests ---


class FakeMemory:
    """In-memory MemoryStore stub for testing agent integration."""

    def __init__(self) -> None:
        self.turns: dict[str, list[tuple[str, str]]] = {}

    async def get_recent(self, session_id: str, limit: int = 10):
        from memory.base import ConversationTurn

        rows = self.turns.get(session_id, [])[-limit:]
        return [ConversationTurn(role=r, content=c, created_at=0.0) for r, c in rows]

    async def append(self, session_id: str, role: str, content: str) -> None:
        self.turns.setdefault(session_id, []).append((role, content))

    async def clear(self, session_id: str) -> None:
        self.turns.pop(session_id, None)


@pytest.mark.asyncio
async def test_agent_prepends_history_from_memory():
    llm = FakeLLM(tokens=["ok"])
    mem = FakeMemory()
    await mem.append("s1", "user", "earlier question")
    await mem.append("s1", "assistant", "earlier answer")

    agent = SimpleLLMAgent(llm, memory=mem)
    async for _ in agent.chat_stream(
        [{"role": "user", "content": "new question"}],
        session_id="s1",
    ):
        pass

    # LLM should have received history + new message (3 total)
    assert llm.last_messages is not None
    assert len(llm.last_messages) == 3
    assert llm.last_messages[0] == {"role": "user", "content": "earlier question"}
    assert llm.last_messages[1] == {"role": "assistant", "content": "earlier answer"}
    assert llm.last_messages[2] == {"role": "user", "content": "new question"}


@pytest.mark.asyncio
async def test_agent_persists_exchange_to_memory():
    llm = FakeLLM(tokens=["hi", " there"])
    mem = FakeMemory()

    agent = SimpleLLMAgent(llm, memory=mem)
    async for _ in agent.chat_stream(
        [{"role": "user", "content": "hello"}],
        session_id="s1",
    ):
        pass

    stored = mem.turns["s1"]
    assert stored == [("user", "hello"), ("assistant", "hi there")]


@pytest.mark.asyncio
async def test_agent_sessions_isolated_in_memory():
    llm = FakeLLM(tokens=["reply"])
    mem = FakeMemory()
    await mem.append("other", "user", "other-session-msg")

    agent = SimpleLLMAgent(llm, memory=mem)
    async for _ in agent.chat_stream(
        [{"role": "user", "content": "q"}],
        session_id="s1",
    ):
        pass

    # "other" session history should NOT leak into s1's LLM call
    assert llm.last_messages == [{"role": "user", "content": "q"}]


@pytest.mark.asyncio
async def test_agent_without_memory_is_zero_change():
    """Verify the memory=None path still proxies cleanly (S0 compat)."""
    llm = FakeLLM(tokens=["pass-through"])
    agent = SimpleLLMAgent(llm)  # no memory

    collected = []
    async for tok in agent.chat_stream(
        [{"role": "user", "content": "hi"}],
        session_id="s1",
    ):
        collected.append(tok)

    assert collected == ["pass-through"]
    assert llm.last_messages == [{"role": "user", "content": "hi"}]
