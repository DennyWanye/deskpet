"""P2-1-S3 Task 5: control-WS ``provider_test_connection`` handler.

The test doesn't boot ``main.app`` — ``main.py`` pulls in faster_whisper
and cosyvoice, which aren't always present in the backend-dev venv. We
import the handler as a standalone coroutine and drive it with a fake
WebSocket. That covers the reachable path shape + the validation rules,
which is what matters for the slice; slice-wide WS coverage lives in
``test_e2e_integration.py`` (out of scope here).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


class _FakeWS:
    """Minimal stand-in for fastapi.WebSocket.

    Only ``send_json`` is exercised. We keep the history so the assertions
    can target a specific reply shape instead of the last-write-wins value.
    """

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


@pytest.fixture
def handler():
    # Import lazily — same reason as the other tests in this slice.
    from provider_test_connection import handle_provider_test_connection
    return handle_provider_test_connection


async def test_returns_ok_true_when_provider_reports_healthy(handler):
    ws = _FakeWS()
    with patch("provider_test_connection.OpenAICompatibleProvider") as M:
        M.return_value.health_check = AsyncMock(return_value=True)
        await handler(ws, {
            "base_url": "http://fake/v1",
            "api_key": "sk-test",
            "model": "qwen3.6-plus",
        })
    assert len(ws.sent) == 1
    reply = ws.sent[0]
    assert reply["type"] == "provider_test_connection_result"
    assert reply["payload"]["ok"] is True
    assert reply["payload"].get("tested_url", "").endswith("/models")


async def test_returns_ok_false_when_provider_reports_unhealthy(handler):
    ws = _FakeWS()
    with patch("provider_test_connection.OpenAICompatibleProvider") as M:
        M.return_value.health_check = AsyncMock(return_value=False)
        await handler(ws, {
            "base_url": "http://fake/v1",
            "api_key": "sk-test",
            "model": "qwen3.6-plus",
        })
    reply = ws.sent[0]
    assert reply["type"] == "provider_test_connection_result"
    assert reply["payload"]["ok"] is False


async def test_validation_rejects_missing_fields(handler):
    ws = _FakeWS()
    # Missing model + api_key.
    await handler(ws, {"base_url": "http://fake/v1"})
    reply = ws.sent[0]
    assert reply["type"] == "provider_test_connection_result"
    assert reply["payload"]["ok"] is False
    assert "required" in (reply["payload"].get("error") or "").lower()


async def test_exception_in_provider_is_reported_not_raised(handler):
    ws = _FakeWS()
    with patch("provider_test_connection.OpenAICompatibleProvider") as M:
        M.return_value.health_check = AsyncMock(side_effect=RuntimeError("boom"))
        await handler(ws, {
            "base_url": "http://fake/v1",
            "api_key": "sk-test",
            "model": "qwen3.6-plus",
        })
    reply = ws.sent[0]
    assert reply["payload"]["ok"] is False
    assert "boom" in (reply["payload"].get("error") or "")
