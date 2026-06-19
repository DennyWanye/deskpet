# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""flag-off 字节级基线 (plan DoD#2): js_render 默认关时,抓取链路行为与引入前一致。

把"字节级不变"做成**可证伪**的自动断言,而非肉眼:
  ① default_extract 返回 dict 的 extractor == "trafilatura"(没走任何渲染分支);
  ② 全程没调用 cdp_edge_render(渲染适配器零触达);
  ③ 没有 import crawl4ai(dev 档懒 import,flag-off 下绝不触发);
  ④ 默认未开 jina,空壳站照旧 ok=False。
"""
from __future__ import annotations

import sys

import pytest

from deskpet.tools import research_tools as r


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    # 显式确保默认关(不依赖外部 config.toml 状态)
    monkeypatch.setattr(r, "_js_render_enabled", lambda: False)
    monkeypatch.setattr(r, "_jina_enabled", lambda: False)
    r._reset_js_render_budget()
    yield
    r._reset_js_render_budget()


_BIG_SHELL = ("<html><head><title>站</title></head><body><div id='app'></div>"
              "<script>/*" + ("x" * 21000) + "*/render()</script></body></html>")
_ARTICLE = ("<html><head><title>好文</title></head><body><article><p>"
            + ("这是充实的静态正文。" * 60) + "</p></article></body></html>")


class _Resp:
    def __init__(self, text):
        self.text = text
        self.url = "x"

    def raise_for_status(self):
        return None


def _client(html):
    class _C:
        async def get(self, url, **kw):
            return _Resp(html)

        async def aclose(self):
            return None
    return _C()


@pytest.mark.asyncio
async def test_flag_off_static_site_identical(monkeypatch):
    """静态站: flag-off 下 extractor 仍 trafilatura,渲染适配器零触达。"""
    import deskpet.tools.research_cdp_edge as ce_mod
    called = {"n": 0}

    async def _spy(url, *, timeout=20.0):
        called["n"] += 1
        return "<html><body><p>SHOULD NOT BE CALLED</p></body></html>"

    monkeypatch.setattr(ce_mod, "cdp_edge_render", _spy)
    out = await r.default_extract("https://static.com/a", client=_client(_ARTICLE))
    assert out["ok"] is True
    assert out["extractor"] == "trafilatura"   # ① 没走渲染
    assert called["n"] == 0                      # ② 渲染适配器零触达


@pytest.mark.asyncio
async def test_flag_off_js_shell_no_render(monkeypatch):
    """大 JS 空壳: flag-off 下也绝不触发渲染,行为与引入渲染前一致(ok=False)。"""
    import deskpet.tools.research_cdp_edge as ce_mod
    called = {"n": 0}

    async def _spy(url, *, timeout=20.0):
        called["n"] += 1
        return "x" * 9999

    monkeypatch.setattr(ce_mod, "cdp_edge_render", _spy)
    out = await r.default_extract("https://spa.com/x", client=_client(_BIG_SHELL))
    assert called["n"] == 0                      # ② 渲染零触达
    assert out["ok"] is False                     # ④ 空壳 + 渲染/jina 全关 → 失败(原行为)


@pytest.mark.asyncio
async def test_flag_off_no_crawl4ai_import(monkeypatch):
    """flag-off 下绝不 import crawl4ai(dev 档懒 import,不该被触发)。"""
    sys.modules.pop("crawl4ai", None)
    out = await r.default_extract("https://spa.com/x", client=_client(_BIG_SHELL))
    assert "crawl4ai" not in sys.modules          # ③ 没 import crawl4ai
    assert out["ok"] is False
