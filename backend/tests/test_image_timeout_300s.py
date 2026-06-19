# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""2026-06-11 gpt-image-2 超时根因修复 TG。

中转站侧证据链（Caddy 访问日志 × RequestLog）：服务端 28/30 次成功，
耗时 57~221s；桌宠 httpx 60~70s 把连接掐断 → 客户端报超时挂掉 +
服务端照样生成完并扣费（$0.15/次隐性扣费）。

修复语义（本文件锁死）：
- read 超时 = 300s，与服务端路由超时 timeoutMs=300000 对齐
- 读超时 / 504 不重试（重试 = 重复扣费）
- 连接超时 / 断连 / SSL / 502 / 503 真瞬时错误仍重试
- payload 带 quality（默认 medium，降耗时；中转层全字段透传）
"""
from __future__ import annotations

import base64
import json
import ssl

import httpx
import pytest

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAeImBZsAAAAASUVORK5CYII="
)
_B64 = base64.b64encode(_PNG).decode()


class _Resp:
    def __init__(self, status: int, payload: dict):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    """Drop-in for httpx.Client; scripted per test via class attrs."""

    init_kwargs: dict | None = None
    post_calls = 0
    post_fn = None  # callable(url, **k) -> _Resp（或 raise）
    last_payload: dict | None = None

    def __init__(self, *a, **k):
        type(self).init_kwargs = k

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, **k):
        type(self).post_calls += 1
        type(self).last_payload = k.get("json")
        return type(self).post_fn(url, **k)

    def get(self, url, **k):
        raise AssertionError("url download path not used in these tests")


@pytest.fixture
def img_mod(monkeypatch):
    import deskpet.tools.image_tools as m

    _FakeClient.init_kwargs = None
    _FakeClient.post_calls = 0
    _FakeClient.post_fn = None
    _FakeClient.last_payload = None
    monkeypatch.setattr(m.httpx, "Client", _FakeClient)
    monkeypatch.setattr(
        m, "_resolve_endpoint", lambda: ("https://relay.example/v1", "tsk_test")
    )
    monkeypatch.setattr(m.time, "sleep", lambda *_a: None)  # no real backoff
    return m


def test_timeout_read_300s_aligned_with_relay_route(img_mod):
    """read=300s 覆盖现网最长 221s；connect 短超时保住瞬时重试语义。"""
    m = img_mod
    assert m._TIMEOUT.read == 300.0
    assert m._TIMEOUT.connect == 10.0
    _FakeClient.post_fn = lambda url, **k: _Resp(200, {"data": [{"b64_json": _B64}]})
    png, err = m._generate_png("一只猫", m._DEFAULT_SIZE, m._DEFAULT_MODEL)
    assert err is None and png == _PNG
    # httpx.Client 真的拿到了这个 Timeout 对象
    assert _FakeClient.init_kwargs.get("timeout") is m._TIMEOUT


def test_read_timeout_does_not_retry(img_mod):
    """等满 300s 没结果 → 服务端仍会生成并扣费，重试 = 双倍烧钱。"""
    m = img_mod

    def _raise_read_timeout(url, **k):
        raise httpx.ReadTimeout("timed out")

    _FakeClient.post_fn = _raise_read_timeout
    png, hint = m._generate_png("x", m._DEFAULT_SIZE, m._DEFAULT_MODEL)
    assert png is None
    assert _FakeClient.post_calls == 1  # 没有第二次
    assert "300" in hint and "不重试" in hint


def test_connect_timeout_still_retries(img_mod):
    """连接没建立（10s connect 超时）：没打到生成、无扣费风险 → 重试。"""
    m = img_mod

    def _raise_connect_timeout(url, **k):
        raise httpx.ConnectTimeout("connect timed out")

    _FakeClient.post_fn = _raise_connect_timeout
    png, hint = m._generate_png("x", m._DEFAULT_SIZE, m._DEFAULT_MODEL)
    assert png is None
    assert _FakeClient.post_calls == m._MAX_ATTEMPTS
    assert "连试" in hint


def test_504_does_not_retry(img_mod):
    """504 = 上游已耗尽服务端 300s 预算，再来一轮纯属白烧。"""
    m = img_mod
    _FakeClient.post_fn = lambda url, **k: _Resp(504, {})
    png, hint = m._generate_png("x", m._DEFAULT_SIZE, m._DEFAULT_MODEL)
    assert png is None
    assert _FakeClient.post_calls == 1
    assert "504" in hint


def test_502_retries_then_succeeds(img_mod):
    """502/503 仍是瞬时可重试（网关抖动，未到生成阶段）。"""
    m = img_mod

    def _flaky(url, **k):
        if _FakeClient.post_calls == 1:
            return _Resp(502, {})
        return _Resp(200, {"data": [{"b64_json": _B64}]})

    _FakeClient.post_fn = _flaky
    png, err = m._generate_png("x", m._DEFAULT_SIZE, m._DEFAULT_MODEL)
    assert err is None and png == _PNG
    assert _FakeClient.post_calls == 2


def test_ssl_error_still_retries(img_mod):
    """SSL 瞬时错误保留重试（2026-06-09 B-1 语义不回退）。"""
    m = img_mod

    def _raise_ssl(url, **k):
        raise ssl.SSLError("UNEXPECTED_EOF_WHILE_READING")

    _FakeClient.post_fn = _raise_ssl
    png, hint = m._generate_png("x", m._DEFAULT_SIZE, m._DEFAULT_MODEL)
    assert png is None
    assert _FakeClient.post_calls == m._MAX_ATTEMPTS


def test_payload_carries_quality_default_medium(img_mod):
    """quality 透传到上游（默认 medium，config [image].quality 可覆盖）。"""
    m = img_mod
    _FakeClient.post_fn = lambda url, **k: _Resp(200, {"data": [{"b64_json": _B64}]})
    png, err = m._generate_png("x", m._DEFAULT_SIZE, m._DEFAULT_MODEL)
    assert err is None
    assert _FakeClient.last_payload["quality"] == "medium"
    assert _FakeClient.last_payload["size"] == "1024x1024"


def test_tool_timeout_covers_worst_retry_budget(img_mod):
    """registry 总超时必须 > 最坏预算（退避 + 读满 300s），否则 handler
    会在请求跑完前被 registry 杀掉。"""
    m = img_mod
    worst = m._TIMEOUT.read + sum(m._RETRY_BACKOFF) + m._TIMEOUT.connect
    assert m._TOOL_TIMEOUT_S > worst


def test_bypasses_env_proxy_by_default(img_mod):
    """本机代理（Clash 等）会把 60s+ 零流量的出图连接掐断（实测 ~64s
    RemoteProtocolError）→ 默认 trust_env=False 直连中转站。"""
    m = img_mod
    _FakeClient.post_fn = lambda url, **k: _Resp(200, {"data": [{"b64_json": _B64}]})
    png, err = m._generate_png("x", m._DEFAULT_SIZE, m._DEFAULT_MODEL)
    assert err is None
    assert _FakeClient.init_kwargs.get("trust_env") is False


def test_trust_env_proxy_config_overridable(img_mod, monkeypatch):
    """网络环境必须走代理的用户可用 [image].trust_env_proxy=true 改回。"""
    m = img_mod
    monkeypatch.setattr(m, "_trust_env_proxy", lambda: True)
    _FakeClient.post_fn = lambda url, **k: _Resp(200, {"data": [{"b64_json": _B64}]})
    png, err = m._generate_png("x", m._DEFAULT_SIZE, m._DEFAULT_MODEL)
    assert err is None
    assert _FakeClient.init_kwargs.get("trust_env") is True


def test_missing_api_key_logs_warning(img_mod, monkeypatch, caplog):
    """对应中转站发现的裸 401：key 解析失败时要有日志可查，不静默裸发。"""
    import logging

    m = img_mod
    monkeypatch.setattr(m, "_resolve_endpoint", lambda: ("https://relay.example/v1", None))
    _FakeClient.post_fn = lambda url, **k: _Resp(200, {"data": [{"b64_json": _B64}]})
    with caplog.at_level(logging.WARNING):
        png, err = m._generate_png("x", m._DEFAULT_SIZE, m._DEFAULT_MODEL)
    assert err is None
    assert any("Authorization" in r.message for r in caplog.records)
