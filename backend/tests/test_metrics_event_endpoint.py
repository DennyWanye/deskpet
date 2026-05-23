"""WI-T1.7 — POST /metrics/event 端点测试（前端 ArtifactCard 埋点 sink）。"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    # 强制 DEV_MODE = 1 让 /metrics/event 跳过 auth
    monkeypatch.setenv("DESKPET_DEV_MODE", "1")
    # 重定向 user_data_dir 到 tmp 防止污染真实 metrics.jsonl
    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(tmp_path))
    # 重置 metric_sink 单例
    import observability.metrics_sink as ms
    ms._default_sink = None  # type: ignore[attr-defined]

    from main import app
    return TestClient(app)


def test_metrics_event_accepts_valid_payload(client):
    r = client.post("/metrics/event", json={
        "event": "artifact_action",
        "detail": {
            "action_id": "open",
            "tool_name": "ppt_create",
            "ok": True,
        },
    })
    assert r.status_code == 204


def test_metrics_event_rejects_missing_event(client):
    r = client.post("/metrics/event", json={"detail": {"x": 1}})
    assert r.status_code == 400


def test_metrics_event_rejects_non_dict_detail(client):
    r = client.post("/metrics/event", json={
        "event": "x", "detail": "not a dict",
    })
    assert r.status_code == 400


def test_metrics_event_rejects_invalid_json(client):
    r = client.post(
        "/metrics/event",
        data=b"not json",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400


def test_metrics_event_writes_to_jsonl(client, monkeypatch):
    """端到端：POST → metrics_sink.record() 被调（用 spy 验，不依赖 main
    module 重 init 后 sink 路径）。"""
    import observability.metrics_sink as ms
    captured: list[tuple] = []
    orig_record = ms.record

    def _spy(event, detail=None):
        captured.append((event, detail))
        return orig_record(event, detail)
    monkeypatch.setattr(ms, "record", _spy)

    r = client.post("/metrics/event", json={
        "event": "artifact_action",
        "detail": {"action_id": "open", "tool_name": "ppt_create", "ok": True},
    })
    assert r.status_code == 204
    assert len(captured) == 1
    event, detail = captured[0]
    assert event == "artifact_action"
    assert detail.get("action_id") == "open"
    assert detail.get("tool_name") == "ppt_create"
    assert detail.get("ok") is True
    # 脱敏验证：detail 不含 path / 路径分隔符（MR-22）
    detail_json = json.dumps(detail)
    assert "\\" not in detail_json
    assert "/" not in detail_json
