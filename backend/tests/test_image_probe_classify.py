# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import httpx


class _Resp:
    def __init__(self, status: int, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class _FakeClient:
    init_kwargs: dict | None = None
    get_status = 200
    get_exc: Exception | None = None
    post_resp: _Resp | None = None

    def __init__(self, *args, **kwargs):
        type(self).init_kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, **kwargs):
        assert url in {
            "https://relay.example/v1/models",
            "https://relay.example/v1/models",
        }
        if type(self).get_exc is not None:
            raise type(self).get_exc
        return _Resp(type(self).get_status)

    def post(self, url, **kwargs):
        assert url == "https://relay.example/v1/images/generations"
        return type(self).post_resp


def test_probe_image_reachable_treats_4xx_as_reachable_and_5xx_or_exception_as_false(
    monkeypatch,
):
    import deskpet.tools.image_tools as m

    monkeypatch.setattr(m.httpx, "Client", _FakeClient)
    monkeypatch.setattr(
        m, "_resolve_relay_base_and_key", lambda: ("https://relay.example/v1", "key")
    )
    monkeypatch.setattr(m, "_trust_env_proxy", lambda: True)

    _FakeClient.get_status = 404
    _FakeClient.get_exc = None
    assert m.probe_image_reachable(timeout_s=1.5) is True
    assert _FakeClient.init_kwargs["trust_env"] is True
    assert _FakeClient.init_kwargs["timeout"].connect == 5.0
    assert _FakeClient.init_kwargs["timeout"].read == 1.5

    _FakeClient.get_status = 503
    assert m.probe_image_reachable(timeout_s=1.5) is False

    _FakeClient.get_exc = httpx.ConnectError("offline")
    assert m.probe_image_reachable(timeout_s=1.5) is False


def test_classify_image_error_prioritizes_status_then_structured_then_message():
    import deskpet.tools.image_tools as m

    assert m._classify_image_error(503, None, None) == "connectivity"
    assert m._classify_image_error(401, {"error": {"code": "model_not_found"}}, None) == "auth"
    assert m._classify_image_error(429, None, None) == "quota"
    assert (
        m._classify_image_error(
            400,
            {"error": {"type": "invalid_request", "code": "model_not_found"}},
            None,
        )
        == "model_unavailable"
    )
    assert (
        m._classify_image_error(400, {"error": {"message": "model 不支持"}}, None)
        == "model_unavailable"
    )
    assert m._classify_image_error(400, {"error": {"code": "content_policy"}}, None) == "content"
    assert m._classify_image_error(None, None, RuntimeError("boom")) == "unknown"


def test_generate_images_failure_result_includes_error_kind(monkeypatch):
    import deskpet.tools.image_tools as m

    monkeypatch.setattr(m.httpx, "Client", _FakeClient)
    monkeypatch.setattr(
        m, "_resolve_endpoint", lambda: ("https://relay.example/v1", "key")
    )
    monkeypatch.setattr(m.time, "sleep", lambda *_args: None)

    _FakeClient.post_resp = _Resp(400, {"error": {"message": "model 不支持"}})

    out = m.generate_images(["slide cover"])

    assert out[0]["prompt"] == "slide cover"
    assert out[0]["path"] is None
    assert out[0]["error"]
    assert out[0]["error_kind"] == "model_unavailable"
