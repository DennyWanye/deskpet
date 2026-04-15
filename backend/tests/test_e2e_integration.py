"""S10 E2E integration tests — FastAPI TestClient with the real app wiring.

These are NOT offline unit tests: they import the actual ``main.app``
which runs the full boot sequence (config, provider registration, crash
reporter, tier detection). What they DO avoid is the network/hardware
layer — LLM/ASR/TTS backends are replaced with in-process fakes via
service_context mutation, so the tests stay fast and hermetic.

Coverage goals (R20):
1. End-to-end chat roundtrip through agent + memory + tool registry.
2. Session isolation — two sessions don't share VoicePipeline state.
3. Cross-channel interrupt dispatch (control WS → audio WS pipeline).
4. Redacting memory wrapper actually catches secrets on the write path.
5. Tool registry confirmation gate rejects high-risk tools by default.
6. Unknown message type returns a structured error, not a crash.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from main import (
    _control_connections,
    _pipelines,
    app,
    service_context,
    SHARED_SECRET,
)


# ---------------------------------------------------------------------
# Fakes — keep tests hermetic, no Ollama / Whisper / edge-tts required.
# ---------------------------------------------------------------------


class FakeLLM:
    """Stand-in for OpenAICompatibleProvider that streams a deterministic reply."""

    def __init__(self, reply: str = "hi from fake llm"):
        self.reply = reply
        self.last_messages: list[dict[str, str]] | None = None

    async def chat_stream(
        self, messages, *, temperature: float = 0.7, max_tokens: int = 2048
    ) -> AsyncIterator[str]:
        self.last_messages = list(messages)
        for ch in self.reply:
            yield ch

    async def health_check(self) -> bool:
        return True


@pytest.fixture
def fake_llm_agent(monkeypatch):
    """Swap the live agent for one backed by FakeLLM + real memory/tools.

    Yields the FakeLLM so tests can inspect what was sent / mutate reply.
    """
    from agent.providers.simple_llm import SimpleLLMAgent
    from agent.providers.tool_using import ToolUsingAgent

    fake = FakeLLM()
    memory = service_context.get("memory_store")
    registry = service_context.get("tool_router")
    new_agent = ToolUsingAgent(
        base=SimpleLLMAgent(fake, memory=memory), registry=registry
    )

    old_agent = service_context.get("agent_engine")
    service_context.register("agent_engine", new_agent)
    try:
        yield fake
    finally:
        service_context.register("agent_engine", old_agent)


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


def test_full_chat_roundtrip_uses_fake_llm(fake_llm_agent):
    """Control WS → chat → agent streams → WS emits chat_response."""
    fake_llm_agent.reply = "你好，我是测试回复"
    client = TestClient(app)
    with client.websocket_connect(
        "/ws/control",
        headers={"X-Shared-Secret": SHARED_SECRET},
        params={"session_id": "e2e_chat"},
    ) as ws:
        ws.send_json({"type": "chat", "payload": {"text": "ping"}})
        msg = ws.receive_json()

    assert msg["type"] == "chat_response"
    assert msg["payload"]["text"] == "你好，我是测试回复"
    # The user's text must have reached the LLM.
    assert fake_llm_agent.last_messages is not None
    assert fake_llm_agent.last_messages[-1]["content"] == "ping"


def test_unknown_message_type_returns_error(fake_llm_agent):
    """Defensive path — junk type doesn't crash the socket."""
    client = TestClient(app)
    with client.websocket_connect(
        "/ws/control", headers={"X-Shared-Secret": SHARED_SECRET}
    ) as ws:
        ws.send_json({"type": "nonsense_xyz"})
        msg = ws.receive_json()
    assert msg["type"] == "error"
    assert "nonsense_xyz" in msg["payload"]["message"]


def test_audio_ws_rejects_without_secret():
    """Audio channel has the same gate as control."""
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/audio") as ws:
            ws.receive_bytes()
    assert exc_info.value.code == 4001


def test_interrupt_dispatched_when_pipeline_present(fake_llm_agent):
    """Control-channel interrupt must reach the registered audio pipeline."""
    # Manually plant a fake pipeline for the session so we don't need
    # to spin up /ws/audio (which would require a live VAD/ASR/TTS).
    class _FakePipeline:
        def __init__(self):
            self.interrupted = False

        def interrupt(self):
            self.interrupted = True

    fake_pipe = _FakePipeline()
    _pipelines["e2e_interrupt"] = fake_pipe
    try:
        client = TestClient(app)
        with client.websocket_connect(
            f"/ws/control?secret={SHARED_SECRET}&session_id=e2e_interrupt"
        ) as ws:
            ws.send_json({"type": "interrupt"})
            ack = ws.receive_json()
        assert ack["type"] == "interrupt_ack"
        assert fake_pipe.interrupted is True
    finally:
        _pipelines.pop("e2e_interrupt", None)


def test_interrupt_without_pipeline_still_acks(fake_llm_agent):
    """No active pipeline → log + ack, not a crash."""
    client = TestClient(app)
    with client.websocket_connect(
        f"/ws/control?secret={SHARED_SECRET}&session_id=e2e_no_pipe"
    ) as ws:
        ws.send_json({"type": "interrupt"})
        ack = ws.receive_json()
    assert ack["type"] == "interrupt_ack"


def test_redacting_memory_store_is_active(fake_llm_agent):
    """A user message containing a secret must be redacted before storage.

    This exercises the real RedactingMemoryStore wrapped around the
    SQLite backend — no mock. We inspect what the LLM actually saw on
    turn 2 (which reads from memory).
    """
    session = "e2e_redact"
    client = TestClient(app)
    with client.websocket_connect(
        f"/ws/control?secret={SHARED_SECRET}&session_id={session}"
    ) as ws:
        # Turn 1: send a fake API key. The agent just echoes.
        ws.send_json(
            {
                "type": "chat",
                "payload": {
                    "text": "my token is sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                },
            }
        )
        ws.receive_json()

        # Turn 2: trigger a reply so memory is re-read into the prompt.
        ws.send_json({"type": "chat", "payload": {"text": "what did I say?"}})
        ws.receive_json()

    # The LLM on turn 2 saw the full prompt including history. Memory
    # should have rewritten the secret into a REDACTED marker.
    msgs = fake_llm_agent.last_messages or []
    serialized = " ".join(m.get("content", "") for m in msgs)
    assert "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" not in serialized
    assert "REDACTED" in serialized


def test_tool_registry_denies_high_risk_by_default():
    """S6 fail-closed: a requires_confirmation=True tool is blocked unless
    an explicit confirm callback approves it. We test via the public
    chat_stream path — the LLM emits <tool>NAME</tool>, the agent sees
    requires_confirmation=True, default confirm deny_all refuses it."""
    import asyncio

    from agent.providers.tool_using import ToolUsingAgent
    from tools.base import ToolSpec
    from tools.registry import ToolRegistry

    class DangerousTool:
        spec = ToolSpec(
            name="delete_all",
            description="pretend-destructive",
            requires_confirmation=True,
        )

        async def invoke(self, **kwargs):  # noqa: ARG002
            return "DESTROYED"

    registry = ToolRegistry()
    registry.register(DangerousTool())

    class _EmittingAgent:
        """Base agent that asks to call delete_all."""

        async def chat_stream(self, messages, *, session_id="default"):  # noqa: ARG002
            yield "sure <tool>delete_all</tool>"

    agent = ToolUsingAgent(
        base=_EmittingAgent(), registry=registry, inject_system_prompt=False
    )

    async def run():
        out = ""
        async for tok in agent.chat_stream(
            [{"role": "user", "content": "drop the table"}],
            session_id="sess",
        ):
            out += tok
        return out

    out = asyncio.run(run())
    assert "refused" in out.lower()
    assert "DESTROYED" not in out


def test_control_connection_tracked_and_released(fake_llm_agent):
    """The control connection must register itself in _control_connections
    for the session and vanish on disconnect — this is how the audio
    pipeline finds its lip-sync sink."""
    session = "e2e_track"
    client = TestClient(app)
    with client.websocket_connect(
        f"/ws/control?secret={SHARED_SECRET}&session_id={session}"
    ) as ws:
        ws.send_json({"type": "ping"})
        ws.receive_json()
        assert session in _control_connections
    # After context exits the WS is closed; the server pops it in the
    # disconnect handler.
    assert session not in _control_connections
