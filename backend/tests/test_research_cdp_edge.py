# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""CDP Edge JS 渲染适配器测试。"""
from __future__ import annotations

import os
import warnings

import pytest
from pytest import PytestUnknownMarkWarning

from deskpet.tools import research_cdp_edge as cdp

warnings.filterwarnings(
    "ignore",
    message="Unknown pytest.mark.live.*",
    category=PytestUnknownMarkWarning,
)


def _reset_state(monkeypatch):
    monkeypatch.setattr(cdp, "_EDGE_EXE_CACHE", cdp._UNSET)
    monkeypatch.setattr(cdp, "_proc", None)
    monkeypatch.setattr(cdp, "_ws", None)
    monkeypatch.setattr(cdp, "_port", None)
    monkeypatch.setattr(cdp, "_user_data_dir", None)


def test_cdp_edge_available_no_edge(monkeypatch):
    _reset_state(monkeypatch)
    monkeypatch.setattr(cdp, "_find_edge_executable", lambda: None)

    assert cdp.cdp_edge_available() is False


@pytest.mark.asyncio
async def test_cdp_edge_render_unavailable_returns_none(monkeypatch):
    _reset_state(monkeypatch)
    monkeypatch.setattr(cdp, "_find_edge_executable", lambda: None)

    assert await cdp.cdp_edge_render("http://x") is None


@pytest.mark.asyncio
async def test_cdp_edge_render_handles_launch_failure(monkeypatch):
    _reset_state(monkeypatch)
    monkeypatch.setattr(cdp, "_find_edge_executable", lambda: "C:/fake/msedge.exe")

    def _boom(*args, **kwargs):
        raise RuntimeError("launch failed")

    monkeypatch.setattr(cdp, "_start_edge_process", _boom)

    assert await cdp.cdp_edge_render("http://x") is None


@pytest.mark.asyncio
async def test_cdp_edge_render_eval_timeout_returns_none(monkeypatch):
    """浏览器在、但渲染(navigate/eval)超时 → 返 None 不抛,且不杀常驻浏览器(可复用)。"""
    import asyncio as _asyncio
    _reset_state(monkeypatch)

    async def _slow_render(url, timeout):
        raise _asyncio.TimeoutError()   # 模拟渲染中超时

    monkeypatch.setattr(cdp, "_render_once", _slow_render)
    monkeypatch.setattr(cdp, "_browser_running", lambda: True)   # 浏览器仍活
    reset_called = {"n": 0}

    async def _spy_reset(*a, **k):
        reset_called["n"] += 1

    monkeypatch.setattr(cdp, "_reset_browser_state", _spy_reset)
    assert await cdp.cdp_edge_render("http://x", timeout=1.0) is None
    assert reset_called["n"] == 0   # 单次渲染失败不该重置(杀)常驻浏览器


@pytest.mark.live
@pytest.mark.asyncio
async def test_cdp_edge_render_live_js_site():
    if not os.environ.get("DESKPET_LIVE_CDP_EDGE"):
        pytest.skip("set DESKPET_LIVE_CDP_EDGE=1 to run live CDP smoke")
    if not cdp.cdp_edge_available():
        pytest.skip("Edge not available")

    html = await cdp.cdp_edge_render("https://quotes.toscrape.com/js/", timeout=25.0)
    try:
        assert html
        assert "The world as we have created it" in html
    finally:
        await cdp.shutdown_cdp_edge()
