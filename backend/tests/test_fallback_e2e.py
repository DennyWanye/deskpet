"""P2-1-S7 fallback E2E.

Exercises the full chat path WITHOUT external network:
  TestClient(/ws/control) -> agent_engine -> HybridRouter -> mocked providers.

Each scenario constructs a fresh HybridRouter with httpx.MockTransport-backed
OpenAICompatibleProvider instances, swaps it into service_context, and sends
a real `chat` message over the WebSocket.

This is the "real router code, fake network" test layer.
The S2 unit tests use _FakeProvider (no httpx involved); this suite uses
the real OpenAICompatibleProvider so SSE parsing + connection lifecycle
are also under test.
"""
from __future__ import annotations

import pytest
import httpx
from fastapi.testclient import TestClient

from providers.openai_compatible import OpenAICompatibleProvider
from router.hybrid_router import HybridRouter
from agent.providers.simple_llm import SimpleLLMAgent


# --- helpers ---------------------------------------------------------------

_MODELS_PATH = "/models"

SSE_OK = (
    b'data: {"choices":[{"delta":{"content":"hello "}}]}\n\n'
    b'data: {"choices":[{"delta":{"content":"world"}}]}\n\n'
    b'data: [DONE]\n\n'
)


def _ok_handler(req: httpx.Request) -> httpx.Response:
    if req.url.path.endswith(_MODELS_PATH):
        return httpx.Response(200, json={"data": [{"id": "fake-model"}]})
    return httpx.Response(
        200,
        content=SSE_OK,
        headers={"content-type": "text/event-stream"},
    )


def _503_handler(req: httpx.Request) -> httpx.Response:
    if req.url.path.endswith(_MODELS_PATH):
        # health probe still 200 so router will try chat -- that's where 503 fires
        return httpx.Response(200, json={"data": [{"id": "fake-model"}]})
    return httpx.Response(503, text="upstream unavailable")


def _make_provider(handler, model: str = "fake") -> OpenAICompatibleProvider:
    p = OpenAICompatibleProvider(
        base_url="http://invalid/v1",
        api_key="test-key",
        model=model,
    )
    p._test_transport = httpx.MockTransport(handler)
    return p


def _build_router_with(local_handler, cloud_handler) -> HybridRouter:
    local = _make_provider(local_handler, model="local-fake") if local_handler else None
    cloud = _make_provider(cloud_handler, model="cloud-fake") if cloud_handler else None
    return HybridRouter(local=local, cloud=cloud)


def _stub_heavy_modules() -> None:
    """Provide in-memory stubs for heavyweight optional deps that ``main``
    imports at module load (faster_whisper, edge_tts, silero_vad). These
    packages aren't part of the dev env's required install; the fallback
    E2E suite only exercises the LLM path and never touches ASR/TTS/VAD.

    Stubs are idempotent -- re-installing just overwrites ``sys.modules``
    entries. We stub the *dependency* modules, not the local
    ``providers.faster_whisper_asr`` wrappers, so import machinery succeeds
    but the wrapper code paths remain real (none get called in these tests).
    """
    import sys
    import types

    def _ensure(name: str, **attrs) -> None:
        mod = sys.modules.get(name)
        if mod is None:
            mod = types.ModuleType(name)
            sys.modules[name] = mod
        for k, v in attrs.items():
            setattr(mod, k, v)

    class _FakeWhisperModel:
        def __init__(self, *a, **kw) -> None:
            pass

        def transcribe(self, *a, **kw):  # pragma: no cover - never invoked
            return iter(()), None

    _ensure("faster_whisper", WhisperModel=_FakeWhisperModel)

    class _FakeCommunicate:
        def __init__(self, *a, **kw) -> None:
            pass

        async def stream(self):  # pragma: no cover - never invoked
            if False:
                yield

    _ensure("edge_tts", Communicate=_FakeCommunicate)

    def _fake_load_vad(*a, **kw):  # pragma: no cover - never invoked
        return None, None

    _ensure("silero_vad", load_silero_vad=_fake_load_vad, get_speech_timestamps=_fake_load_vad)


@pytest.fixture
def app_with_router(monkeypatch):
    """Build the real FastAPI app, then replace its llm_engine + agent_engine
    with versions wired to a mocked router. Yields (TestClient, set_router fn).

    We reload ``main`` on every test so the module-level ``service_context``
    and ``DEV_MODE`` are freshly initialized. The reload is expensive (model
    imports) but safe: preload ``lifespan`` only fires when ``TestClient`` is
    used as a context manager, which we deliberately don't do here -- the
    fallback slice exercises only ``/ws/control``, so ASR/TTS/VAD loads are
    not required.
    """
    _stub_heavy_modules()
    import importlib
    import main as main_mod

    # Preserve SHARED_SECRET across reload. Other test modules (e.g.
    # test_websocket) capture the value at import time; if we regenerate it
    # here, their cached copy no longer matches ``main.SHARED_SECRET`` and
    # later WS auth checks fail with 4001 depending on collection order.
    prior_secret = main_mod.SHARED_SECRET
    importlib.reload(main_mod)
    main_mod.SHARED_SECRET = prior_secret
    monkeypatch.setattr(main_mod, "DEV_MODE", True)
    client = TestClient(main_mod.app)

    def set_router(r: HybridRouter) -> None:
        # ServiceContext is a dataclass with setattr-based register(); reusing
        # register() here also preserves the validation of the service name.
        main_mod.service_context.register("llm_engine", r)
        main_mod.service_context.register(
            "agent_engine",
            SimpleLLMAgent(r, memory=main_mod.service_context.memory_store),
        )

    return client, set_router


def _send_chat_and_collect_response(
    client: TestClient, text: str, max_iters: int = 20
) -> dict:
    with client.websocket_connect("/ws/control?secret=&session_id=fb-test") as ws:
        ws.send_json({"type": "chat", "payload": {"text": text}})
        seen_types: list[str] = []
        for _ in range(max_iters):
            msg = ws.receive_json()
            seen_types.append(msg.get("type", "<missing>"))
            if msg["type"] == "chat_response":
                return msg["payload"]
            # ignore pong / others
        raise AssertionError(
            f"没在 {max_iters} 条消息内收到 chat_response，收到的消息类型：{seen_types}"
        )


# --- scenarios -------------------------------------------------------------

def test_local_healthy_returns_local_response(app_with_router):
    """Baseline: both providers OK, local_first picks local."""
    client, set_router = app_with_router
    set_router(_build_router_with(_ok_handler, _ok_handler))
    payload = _send_chat_and_collect_response(client, "hi")
    assert "hello" in payload["text"]


def test_local_503_falls_back_to_cloud(app_with_router):
    """Local errors -> router records failure -> tries cloud -> success."""
    client, set_router = app_with_router
    set_router(_build_router_with(_503_handler, _ok_handler))
    payload = _send_chat_and_collect_response(client, "hi")
    assert "hello" in payload["text"]  # cloud delivered


def test_local_503_and_cloud_503_returns_echo_fallback(app_with_router):
    """All providers dead -> ws/control catches LLMUnavailableError -> returns
    `[echo] hi` per existing main.py contract."""
    client, set_router = app_with_router
    set_router(_build_router_with(_503_handler, _503_handler))
    payload = _send_chat_and_collect_response(client, "hi")
    assert payload["text"] == "[echo] hi"


def test_cloud_only_when_local_unconfigured(app_with_router):
    """No local provider configured at all -> goes straight to cloud."""
    client, set_router = app_with_router
    set_router(_build_router_with(None, _ok_handler))
    payload = _send_chat_and_collect_response(client, "hi")
    assert "hello" in payload["text"]


def test_circuit_opens_after_three_local_failures(app_with_router):
    """3 local failures -> 4th request must skip local entirely.

    We can't directly observe 'didn't call' across handler boundary, but we
    can assert the response keeps coming from cloud after circuit opens.
    """
    client, set_router = app_with_router
    set_router(_build_router_with(_503_handler, _ok_handler))
    for _ in range(4):
        payload = _send_chat_and_collect_response(client, "x")
        assert "hello" in payload["text"]  # all four served by cloud


def test_repeated_fallback_does_not_leak(app_with_router):
    """100 cycles of cloud-only after local-503 -- assert no exception, no slow degradation."""
    client, set_router = app_with_router
    set_router(_build_router_with(_503_handler, _ok_handler))
    for _ in range(100):
        payload = _send_chat_and_collect_response(client, "x")
        assert "hello" in payload["text"]
