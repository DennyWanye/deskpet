"""TDD: generate_image tool — gpt-image-2 via chinzy, save to workspace,
open with OS viewer.

httpx is mocked (no real API / no cost / no external dependency).
Plan: plans/2026-05-16-generate-image-tool.md
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

# 1×1 transparent PNG
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAeImBZsAAAAASUVORK5CYII="
)
_B64 = base64.b64encode(_PNG).decode()


class _Resp:
    def __init__(self, status: int, payload: dict | bytes):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload

    @property
    def content(self):
        return self._payload if isinstance(self._payload, bytes) else b""

    def raise_for_status(self):
        pass


class _FakeClient:
    """Drop-in for httpx.Client; scripted by class attrs per test."""

    post_resp: _Resp | None = None
    get_resp: _Resp | None = None

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    last_payload: dict | None = None

    def post(self, url, **k):
        assert "/images/generations" in url
        type(self).last_payload = k.get("json")
        return type(self).post_resp

    def get(self, url, **k):
        return type(self).get_resp


@pytest.fixture
def img_mod(monkeypatch, tmp_path):
    """Import the tool with httpx + workspace + endpoint stubbed."""
    import importlib

    import deskpet.tools.image_tools as m

    monkeypatch.setattr(m.httpx, "Client", _FakeClient)
    monkeypatch.setattr(
        m, "_resolve_endpoint", lambda: ("https://chinzy.com/v1", "tsk_test")
    )
    monkeypatch.setattr(m, "_workspace_dir", lambda: tmp_path)
    # never actually pop a viewer window in tests
    monkeypatch.setattr(m, "_open_file", lambda p: True)
    _FakeClient.post_resp = None
    _FakeClient.get_resp = None
    importlib.reload  # noqa: B018 — keep ref; module already imported
    return m, tmp_path


def test_missing_prompt_returns_error(img_mod):
    m, _ = img_mod
    out = json.loads(m._handle_generate_image({}, ""))
    assert out["ok"] is False
    assert "prompt" in (out.get("error", "") + out.get("hint", "")).lower()


def test_b64_response_saves_png_to_workspace(img_mod):
    m, ws = img_mod
    _FakeClient.post_resp = _Resp(200, {"data": [{"b64_json": _B64}]})
    out = json.loads(m._handle_generate_image({"prompt": "一只猫"}, ""))
    # confirmed chinzy contract: OpenAI Images API, explicit b64_json
    assert _FakeClient.last_payload["model"] == "gpt-image-2"
    assert _FakeClient.last_payload["response_format"] == "b64_json"
    assert _FakeClient.last_payload["n"] == 1
    assert out["ok"] is True
    p = Path(out["path"])
    assert p.exists() and p.suffix == ".png"
    assert p.parent == ws  # saved INTO workspace (D3-safe)
    assert p.read_bytes() == _PNG
    assert out["opened"] is True


def test_url_response_downloads_and_saves(img_mod):
    m, ws = img_mod
    _FakeClient.post_resp = _Resp(200, {"data": [{"url": "https://x/y.png"}]})
    _FakeClient.get_resp = _Resp(200, _PNG)
    out = json.loads(m._handle_generate_image({"prompt": "海报"}, ""))
    assert out["ok"] is True
    assert Path(out["path"]).read_bytes() == _PNG


def test_api_non_200_returns_error_no_raise(img_mod):
    m, _ = img_mod
    _FakeClient.post_resp = _Resp(429, {"error": {"message": "rate limited"}})
    out = json.loads(m._handle_generate_image({"prompt": "x"}, ""))
    assert out["ok"] is False
    assert out.get("hint")


def test_open_failure_still_ok(img_mod, monkeypatch):
    m, _ = img_mod
    _FakeClient.post_resp = _Resp(200, {"data": [{"b64_json": _B64}]})
    monkeypatch.setattr(m, "_open_file", lambda p: False)
    out = json.loads(m._handle_generate_image({"prompt": "x"}, ""))
    assert out["ok"] is True
    assert out["opened"] is False  # best-effort: tool still succeeds


def test_transient_disconnect_retries_then_succeeds(img_mod, monkeypatch):
    """chinzy 'Server disconnected' on attempt 1 → retry → 200 on
    attempt 2. The real 2026-05-16 failure mode (RemoteProtocolError)."""
    import httpx

    m, ws = img_mod
    monkeypatch.setattr(m.time, "sleep", lambda *_a: None)  # no real backoff
    calls = {"n": 0}

    def _flaky_post(self, url, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.RemoteProtocolError(
                "Server disconnected without sending a response."
            )
        return _Resp(200, {"data": [{"b64_json": _B64}]})

    monkeypatch.setattr(_FakeClient, "post", _flaky_post)
    out = json.loads(m._handle_generate_image({"prompt": "复杂场景"}, ""))
    assert calls["n"] == 2  # retried once
    assert out["ok"] is True
    assert Path(out["path"]).read_bytes() == _PNG


def test_all_attempts_disconnect_returns_error(img_mod, monkeypatch):
    import httpx

    m, _ = img_mod
    monkeypatch.setattr(m.time, "sleep", lambda *_a: None)

    def _always_drop(self, url, **k):
        raise httpx.RemoteProtocolError("Server disconnected")

    monkeypatch.setattr(_FakeClient, "post", _always_drop)
    out = json.loads(m._handle_generate_image({"prompt": "x"}, ""))
    assert out["ok"] is False
    # exhausted all attempts (message embeds the configured _MAX_ATTEMPTS)
    assert "连试" in out["hint"] and str(m._MAX_ATTEMPTS) in out["hint"]


def test_4xx_does_not_retry(img_mod, monkeypatch):
    """Deterministic 4xx (auth/quota/param) must NOT retry — fail fast."""
    m, _ = img_mod
    calls = {"n": 0}

    def _post_400(self, url, **k):
        calls["n"] += 1
        return _Resp(400, {"error": {"message": "bad request"}})

    monkeypatch.setattr(_FakeClient, "post", _post_400)
    out = json.loads(m._handle_generate_image({"prompt": "x"}, ""))
    assert calls["n"] == 1  # no retry on 4xx
    assert out["ok"] is False


def test_registered_in_registry():
    from deskpet.tools import registry
    import deskpet.tools.image_tools  # noqa: F401 — triggers registration

    spec = registry.get("generate_image")
    assert spec is not None
    assert spec.toolset == "image"
    assert spec.schema["name"] == "generate_image"


def test_capability_gate_marker_matches_tool_name():
    """The tool name must equal capability_gate's image marker so
    registering it flips image requests REFUSE→PASS."""
    from agent import capability_gate as cg

    img_cap = next(c for c in cg._CAPABILITIES if c.key == "image")
    assert "generate_image" in img_cap.tool_markers
