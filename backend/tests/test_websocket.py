import pytest
from starlette.websockets import WebSocketDisconnect
from fastapi.testclient import TestClient
from main import app, SHARED_SECRET


def _drain_startup_status(ws) -> None:
    """P3-S2: /ws/control sends `startup_status` as its first frame.
    Tests that don't care about that contract drain it before asserting
    on the next frame they actually exercise."""
    msg = ws.receive_json()
    assert msg["type"] == "startup_status"


def test_health_endpoint():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_control_ws_rejects_without_secret():
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/control") as ws:
            ws.receive_json()
    assert exc_info.value.code == 4001


def test_control_ws_accepts_with_secret():
    client = TestClient(app)
    with client.websocket_connect(
        "/ws/control", headers={"X-Shared-Secret": SHARED_SECRET}
    ) as ws:
        _drain_startup_status(ws)
        ws.send_json({"type": "ping"})
        data = ws.receive_json()
        assert data["type"] == "pong"


def test_control_ws_accepts_with_secret_query_param():
    client = TestClient(app)
    with client.websocket_connect(
        f"/ws/control?secret={SHARED_SECRET}"
    ) as ws:
        _drain_startup_status(ws)
        ws.send_json({"type": "ping"})
        data = ws.receive_json()
        assert data["type"] == "pong"


def test_control_ws_echo_chat():
    client = TestClient(app)
    with client.websocket_connect(
        "/ws/control", headers={"X-Shared-Secret": SHARED_SECRET}
    ) as ws:
        _drain_startup_status(ws)
        ws.send_json({"type": "chat", "payload": {"text": "hello"}})
        data = ws.receive_json()
        assert data["type"] == "chat_response"
        assert "text" in data["payload"]
