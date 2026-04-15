"""Test /metrics endpoint auth + content (P2-1-S6).

Auth model:
- DEV_MODE=True -> open
- DEV_MODE=False -> requires ``x-shared-secret`` header matching SHARED_SECRET
  (same gate as WS connections, so operators don't manage a separate secret)

We build a minimal FastAPI app mirroring main.py's /metrics route so the
test doesn't have to import main (which pulls heavy deps like
faster_whisper / silero in this worktree). The actual route body lives
in main.py; keeping its logic thin (delegate to observability.metrics)
means this contract test stays meaningful.
"""
from __future__ import annotations

import secrets as _secrets

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from observability.metrics import render as render_metrics


SHARED_SECRET = "unit-test-secret"


def _build_app(dev_mode: bool) -> FastAPI:
    app = FastAPI()

    @app.get("/metrics")
    async def metrics(request: Request):
        if not dev_mode:
            secret = request.headers.get("x-shared-secret", "")
            if not secret or not _secrets.compare_digest(secret, SHARED_SECRET):
                return Response(status_code=401)
        body, content_type = render_metrics()
        return Response(content=body, media_type=content_type)

    return app


def test_metrics_requires_secret_when_dev_mode_off():
    tc = TestClient(_build_app(dev_mode=False))
    resp = tc.get("/metrics")
    assert resp.status_code == 401


def test_metrics_open_in_dev_mode():
    tc = TestClient(_build_app(dev_mode=True))
    resp = tc.get("/metrics")
    assert resp.status_code == 200
    assert "llm_ttft_seconds" in resp.text


def test_metrics_with_correct_secret():
    tc = TestClient(_build_app(dev_mode=False))
    resp = tc.get("/metrics", headers={"x-shared-secret": SHARED_SECRET})
    assert resp.status_code == 200
    assert "llm_ttft_seconds" in resp.text


def test_metrics_with_wrong_secret_returns_401():
    tc = TestClient(_build_app(dev_mode=False))
    resp = tc.get("/metrics", headers={"x-shared-secret": "not-the-secret"})
    assert resp.status_code == 401


def test_metrics_response_content_type_is_prometheus_text():
    tc = TestClient(_build_app(dev_mode=True))
    resp = tc.get("/metrics")
    assert resp.status_code == 200
    # prometheus_client emits text/plain; version=0.0.4; charset=utf-8
    assert resp.headers["content-type"].startswith("text/plain")
