"""P3-S2 — /health and WS /ws/control integration with startup_errors.

Uses FastAPI TestClient to drive the live app. The module-level
`startup_errors` singleton is mutated directly via the public `record`
/ `clear` API to simulate an ASR load failure without needing to
monkey-patch heavy ML deps (torch, ctranslate2).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main
from observability.startup import registry as startup_errors


@pytest.fixture(autouse=True)
def _reset_startup_errors():
    """Each test starts with a clean registry; restore after."""
    startup_errors.clear()
    yield
    startup_errors.clear()


@pytest.fixture
def client():
    return TestClient(main.app)


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def test_health_ok_when_no_startup_errors(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["startup_errors"] == []


def test_health_degraded_when_asr_load_failed(client):
    startup_errors.record(
        "asr_engine",
        RuntimeError("CUDA driver is not available"),
    )
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert len(body["startup_errors"]) == 1
    err = body["startup_errors"][0]
    assert err["engine"] == "asr_engine"
    assert err["error_code"] == "CUDA_UNAVAILABLE"


def test_health_preserves_existing_fields(client):
    """Regression: secret_hint / strategy / cloud_configured must stay."""
    resp = client.get("/health")
    body = resp.json()
    for key in ("status", "secret_hint", "strategy", "cloud_configured",
                "startup_errors"):
        assert key in body


# ---------------------------------------------------------------------------
# WS /ws/control first-frame
# ---------------------------------------------------------------------------

def test_ws_control_first_frame_is_startup_status_clean(client):
    with client.websocket_connect(
        f"/ws/control?secret={main.SHARED_SECRET}&session_id=t1"
    ) as ws:
        first = ws.receive_json()
        assert first["type"] == "startup_status"
        assert first["degraded"] is False
        assert first["errors"] == []


def test_ws_control_first_frame_carries_degraded_errors(client):
    startup_errors.record(
        "asr_engine",
        RuntimeError("CUDA driver is not available"),
    )
    with client.websocket_connect(
        f"/ws/control?secret={main.SHARED_SECRET}&session_id=t2"
    ) as ws:
        first = ws.receive_json()
        assert first["type"] == "startup_status"
        assert first["degraded"] is True
        assert len(first["errors"]) == 1
        assert first["errors"][0]["error_code"] == "CUDA_UNAVAILABLE"
