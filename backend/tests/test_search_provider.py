# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Deep-research V8 — unified search_provider (region-aware DDG)."""
from __future__ import annotations

from pathlib import Path

import pytest

from deskpet.tools import search_provider as sp


# --- region inference (the core Chinese-coverage fix) ---

def test_region_chinese_query_uses_cn_zh():
    assert sp.region_for_query("中国新能源汽车现状") == "cn-zh"


def test_region_english_query_uses_us_en():
    assert sp.region_for_query("state of quantum computing 2026") == "us-en"


def test_region_mixed_cjk_counts_as_chinese():
    assert sp.region_for_query("AI 大模型 benchmark") == "cn-zh"


def test_region_empty_defaults_us_en():
    assert sp.region_for_query("") == "us-en"


# --- pure HTML parser (network-free) ---

_SAMPLE_HTML = """
<div class="result results_links">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&rut=x">Title A</a>
  <a class="result__snippet" href="x">Snippet A here</a>
</div>
<div class="result results_links">
  <a class="result__a" href="https://example.org/b">Title B</a>
  <a class="result__snippet" href="x">Snippet B</a>
</div>
"""


def test_parse_unwraps_uddg_redirect():
    out = sp.parse_ddg_html(_SAMPLE_HTML, max_results=5)
    urls = [r["url"] for r in out]
    assert "https://example.com/a" in urls  # uddg unwrapped
    assert "https://example.org/b" in urls


def test_parse_respects_max_results():
    out = sp.parse_ddg_html(_SAMPLE_HTML, max_results=1)
    assert len(out) == 1


def test_parse_extracts_title_and_snippet():
    out = sp.parse_ddg_html(_SAMPLE_HTML, max_results=5)
    a = next(r for r in out if r["url"].endswith("/a"))
    assert a["title"] == "Title A"
    assert "Snippet A" in a["snippet"]


def test_parse_regex_fallback_matches_selectolax():
    # Force the regex path directly — should find both results too.
    out = sp._parse_ddg_regex(_SAMPLE_HTML, max_results=5)
    assert len(out) == 2
    assert out[0]["title"] == "Title A"


def test_parse_empty_html_returns_empty():
    assert sp.parse_ddg_html("<html></html>", max_results=5) == []


# --- sync search guards (no network) ---

def test_search_empty_query_no_network():
    r = sp.search("   ")
    assert r["count"] == 0 and r["results"] == [] and "error" in r


def test_search_ddg_engine_region(monkeypatch):
    """显式只用 duckduckgo: 中文 query → kl=cn-zh,解析出结果,engine 标注。"""
    captured = {}

    class _FakeResp:
        text = _SAMPLE_HTML

        def raise_for_status(self):
            return None

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def request(self, method, url, **kw):
            captured["method"] = method
            captured["data"] = kw.get("data")
            return _FakeResp()

    monkeypatch.setattr(sp.httpx, "Client", _FakeClient)
    r = sp.search("中文测试查询", max_results=99, engines=["duckduckgo"])
    assert captured["method"] == "POST"
    assert captured["data"]["kl"] == "cn-zh"
    assert r["engine"] == "duckduckgo"
    assert r["count"] == 2


# --- 多引擎: bing/baidu 解析 + 兼容性降级队列 ---

_BING_HTML = """
<ol id="b_results">
  <li class="b_algo"><h2><a href="https://example.com/a">必应标题A</a></h2>
    <div class="b_caption"><p>必应摘要A</p></div></li>
  <li class="b_algo"><h2><a href="https://example.org/b">Bing Title B</a></h2>
    <div class="b_caption"><p>Bing snippet B</p></div></li>
</ol>
"""

_BAIDU_HTML = """
<div class="result c-container"><h3 class="t"><a href="http://www.baidu.com/link?url=ABC">百度标题A</a></h3>
  <div class="c-abstract">百度摘要A</div></div>
<div class="result c-container"><h3 class="t"><a href="http://www.baidu.com/link?url=DEF">百度标题B</a></h3></div>
"""


def test_parse_bing_html():
    out = sp.parse_bing_html(_BING_HTML, max_results=5)
    assert len(out) == 2
    assert out[0]["url"] == "https://example.com/a"
    assert out[0]["title"] == "必应标题A"
    assert "必应摘要A" in out[0]["snippet"]


def test_parse_baidu_html():
    out = sp.parse_baidu_html(_BAIDU_HTML, max_results=5)
    assert len(out) == 2
    assert out[0]["title"] == "百度标题A"
    assert out[0]["url"].startswith("http")


def test_engine_queue_default():
    assert sp._engine_queue() == ["google-cdp"]  # §6.0: 默认只 google-cdp(可达门控),去 bing/ddg


def test_engine_queue_config(monkeypatch):
    import types
    fake = types.SimpleNamespace(config=types.SimpleNamespace(
        raw={"research": {
            "search_engines": ["baidu", "BING", "bing-cdp", "searxng", "garbage"],
            "searxng_url": "https://sx.example/search",
        }}))
    monkeypatch.setitem(__import__("sys").modules, "config", fake)
    assert sp._engine_queue() == ["baidu", "bing", "bing-cdp", "searxng"]  # 清洗+小写+去非法,保序


def test_engine_queue_reads_config_via_load_config_fallback(monkeypatch):
    """2026-06-21 真机修复回归: config 模块**无 config 单例**时(真实运行口径,
    main.py 不注入 config.config),_research_raw/_engine_queue 必须经 load_config(
    resolve_config_path()) 兜底读到 [research].search_engines —— 否则配置开关全失效。"""
    import types
    # fake config 模块: 没有 .config 单例,只有 load_config + resolve_config_path
    fake_cfg_obj = types.SimpleNamespace(raw={"research": {"search_engines": ["baidu", "bing"]}})
    fake = types.SimpleNamespace(
        load_config=lambda *_a, **_k: fake_cfg_obj,
        resolve_config_path=lambda *_a, **_k: "X",
    )
    assert not hasattr(fake, "config")  # 复现真实:无单例
    monkeypatch.setitem(__import__("sys").modules, "config", fake)
    assert sp._engine_queue() == ["baidu", "bing"]   # 经 load_config 兜底读到,非默认


def test_known_engines_includes_browser_and_searxng():
    assert "bing-cdp" in sp._KNOWN_ENGINES
    assert "google-cdp" in sp._KNOWN_ENGINES
    assert "searxng" in sp._KNOWN_ENGINES


def test_search_fallback_queue(monkeypatch):
    """降级队列: bing 失败/空 → 降级到 duckduckgo 出结果,engine 标 duckduckgo。"""
    calls = []

    class _FakeResp:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            return None

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def request(self, method, url, **kw):
            calls.append(url)
            if "bing.com" in url:
                raise sp.httpx.HTTPError("bing blocked")  # 模拟必应被墙
            return _FakeResp(_SAMPLE_HTML)  # ddg 出结果

    monkeypatch.setattr(sp.httpx, "Client", _FakeClient)
    # §6.0: 默认队列已改 google-cdp,本测显式传 bing/ddg 验降级机制本身
    r = sp.search("测试", max_results=5, engines=["bing", "duckduckgo"])
    assert r["engine"] == "duckduckgo"   # 降级成功
    assert r["count"] == 2
    assert any("bing.com" in u for u in calls)  # 确实先试了 bing


def test_search_all_engines_fail(monkeypatch):
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def request(self, method, url, **kw):
            raise sp.httpx.HTTPError("all blocked")

    monkeypatch.setattr(sp.httpx, "Client", _FakeClient)
    r = sp.search("测试", max_results=5)
    assert r["count"] == 0 and r["results"] == [] and "error" in r


@pytest.fixture(autouse=True)
def _reset_search_provider_state(monkeypatch):
    sp.reset_search_cdp_budget()
    sp.reset_search_runtime_state()
    monkeypatch.setattr(sp, "_now", lambda: 0.0)
    yield
    sp.reset_search_cdp_budget()
    sp.reset_search_runtime_state()


class _AsyncClientFail:
    def __init__(self, *a, **k):
        self.requests = []

    async def request(self, method, url, **kw):
        self.requests.append((method, url, kw))
        raise AssertionError("httpx AsyncClient.request should not be called")

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_search_async_bing_cdp_uses_rendered_fixture_without_httpx(monkeypatch):
    fixture = (sp.__file__ and __import__("pathlib").Path(__file__).parent / "fixtures" / "bing_serp_sample.html")
    html = fixture.read_text(encoding="utf-8")
    calls = []

    async def _fake_render(url: str, *, timeout: float):
        calls.append((url, timeout))
        return html

    monkeypatch.setattr(sp, "cdp_edge_render", _fake_render)
    client = _AsyncClientFail()

    results = await sp.search_async("中文测试", engines=["bing-cdp"], client=client, max_results=5)

    assert len(results) >= 2
    assert calls and "bing.com/search" in calls[0][0]
    assert "mkt=zh-CN" in calls[0][0]
    assert calls[0][1] == 8.0
    assert client.requests == []
    assert sp.get_last_engines_hit() == ["bing-cdp"]


@pytest.mark.asyncio
async def test_search_async_bing_cdp_none_degrades_to_next_engine(monkeypatch):
    async def _fake_render(url: str, *, timeout: float):
        return None

    class _Client:
        class _Resp:
            text = _SAMPLE_HTML

            def raise_for_status(self):
                return None

        async def request(self, method, url, **kw):
            return self._Resp()

    monkeypatch.setattr(sp, "cdp_edge_render", _fake_render)

    results = await sp.search_async("test", engines=["bing-cdp", "duckduckgo"], client=_Client())

    assert len(results) == 2
    assert sp.get_last_engines_hit() == ["duckduckgo"]


@pytest.mark.asyncio
async def test_search_async_bing_cdp_captcha_sentinel(monkeypatch):
    captcha_html = "<html><body>verify captcha unusual traffic 机器人</body></html>" + ("x" * 11_000)

    async def _fake_render(url: str, *, timeout: float):
        return captcha_html

    monkeypatch.setattr(sp, "cdp_edge_render", _fake_render)

    results = await sp.search_async("test", engines=["bing-cdp"], client=_AsyncClientFail())

    assert results == []
    assert "bing_cdp_captcha_suspected" in sp.get_last_search_errors()
    assert sp.get_last_engines_hit() == []


def test_parse_google_html_extracts_organic_results():
    html = (Path(__file__).parent / "fixtures" / "google_serp_sample.html").read_text(encoding="utf-8")

    out = sp._parse_google_html(html, max_results=5)

    assert out[:2] == [
        {
            "url": "https://example.com/google-a",
            "title": "Google Result A",
            "snippet": "First Google organic snippet.",
        },
        {
            "url": "https://example.org/google-b",
            "title": "Google Result B",
            "snippet": "Second Google organic snippet.",
        },
    ]


@pytest.mark.asyncio
async def test_search_async_google_cdp_uses_rendered_fixture_without_httpx(monkeypatch):
    html = (Path(__file__).parent / "fixtures" / "google_serp_sample.html").read_text(encoding="utf-8")
    calls = []

    async def _fake_render(url: str, *, timeout: float):
        calls.append((url, timeout))
        return html

    monkeypatch.setattr(sp, "_google_reachable", lambda: True)
    monkeypatch.setattr(sp, "cdp_edge_render", _fake_render)
    client = _AsyncClientFail()

    results = await sp.search_async("中文测试", engines=["google-cdp"], client=client, max_results=5)

    assert len(results) >= 2
    assert calls and "google.com/search" in calls[0][0]
    assert "q=%E4%B8%AD%E6%96%87%E6%B5%8B%E8%AF%95" in calls[0][0]
    assert "hl=zh-CN" in calls[0][0]
    assert calls[0][1] == 8.0
    assert client.requests == []
    assert sp.get_last_engines_hit() == ["google-cdp"]


@pytest.mark.asyncio
async def test_search_async_google_cdp_unreachable_skips_render_and_degrades(monkeypatch):
    render_calls = []

    async def _fake_render(url: str, *, timeout: float):
        render_calls.append((url, timeout))
        raise AssertionError("google-cdp should not render when google is unreachable")

    class _Client:
        class _Resp:
            text = _SAMPLE_HTML

            def raise_for_status(self):
                return None

        async def request(self, method, url, **kw):
            return self._Resp()

    monkeypatch.setattr(sp, "_google_reachable", lambda: False)
    monkeypatch.setattr(sp, "cdp_edge_render", _fake_render)

    results = await sp.search_async("test", engines=["google-cdp", "duckduckgo"], client=_Client())

    assert len(results) == 2
    assert render_calls == []
    assert sp.get_last_engines_hit() == ["duckduckgo"]
    assert "google-cdp: unreachable" in sp.get_last_search_errors()


@pytest.mark.asyncio
async def test_search_async_google_cdp_captcha_sentinel(monkeypatch):
    captcha_html = (
        '<html><body><form action="/sorry/index"><div class="g-recaptcha"></div>'
        "Our systems have detected unusual traffic from your computer network."
        "</body></html>"
        + ("x" * 11_000)
    )

    async def _fake_render(url: str, *, timeout: float):
        return captcha_html

    monkeypatch.setattr(sp, "_google_reachable", lambda: True)
    monkeypatch.setattr(sp, "cdp_edge_render", _fake_render)

    results = await sp.search_async("test", engines=["google-cdp"], client=_AsyncClientFail())

    assert results == []
    assert "google_cdp_captcha_suspected" in sp.get_last_search_errors()
    assert sp.get_last_engines_hit() == []


def test_google_reachable_cache_uses_injected_clock(monkeypatch):
    state = {"now": 100.0, "calls": 0}
    monkeypatch.setattr(sp, "_now", lambda: state["now"])
    sp.reset_google_reachable_cache()

    class _Resp:
        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            state["calls"] += 1
            return _Resp()

    monkeypatch.setattr(sp.httpx, "Client", _Client)

    assert sp._google_reachable() is True
    assert sp._google_reachable() is True
    assert state["calls"] == 1

    state["now"] += sp._GOOGLE_REACHABLE_TTL_SECONDS + 0.1
    assert sp._google_reachable() is True
    assert state["calls"] == 2


def test_engine_queue_searxng_requires_url(monkeypatch):
    import types

    no_url = types.SimpleNamespace(config=types.SimpleNamespace(
        raw={"research": {"search_engines": ["searxng", "bing"]}}))
    monkeypatch.setitem(__import__("sys").modules, "config", no_url)
    assert sp._engine_queue() == ["bing"]

    with_url = types.SimpleNamespace(config=types.SimpleNamespace(
        raw={"research": {"search_engines": ["searxng", "bing"], "searxng_url": "https://sx.example/search"}}))
    monkeypatch.setitem(__import__("sys").modules, "config", with_url)
    assert sp._engine_queue() == ["searxng", "bing"]


@pytest.mark.asyncio
async def test_search_async_searxng_parses_json_when_configured(monkeypatch):
    import types

    fake = types.SimpleNamespace(config=types.SimpleNamespace(
        raw={"research": {"search_engines": ["searxng"], "searxng_url": "https://sx.example/search"}}))
    monkeypatch.setitem(__import__("sys").modules, "config", fake)

    class _Resp:
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {"results": [
                {"url": "https://example.com/a", "title": "A", "content": "Alpha"},
                {"url": "https://example.org/b", "title": "B", "content": "Beta"},
            ]}

    class _Client:
        def __init__(self):
            self.calls = []

        async def request(self, method, url, **kw):
            self.calls.append((method, url, kw))
            return _Resp()

    client = _Client()
    results = await sp.search_async("topic", client=client)

    assert results == [
        {"url": "https://example.com/a", "title": "A", "snippet": "Alpha"},
        {"url": "https://example.org/b", "title": "B", "snippet": "Beta"},
    ]
    assert client.calls[0][0] == "GET"
    assert client.calls[0][1] == "https://sx.example/search"
    assert client.calls[0][2]["params"] == {"q": "topic", "format": "json"}
    assert sp.get_last_engines_hit() == ["searxng"]


def test_parse_searxng_json_is_pure():
    body = {"results": [
        {"url": "https://example.com/a", "title": "A", "content": "Alpha"},
        {"url": "", "title": "bad", "content": "skip"},
        {"url": "https://example.org/b", "title": "B", "content": "Beta"},
    ]}
    assert sp._parse_searxng_json(body, max_results=1) == [
        {"url": "https://example.com/a", "title": "A", "snippet": "Alpha"}
    ]


def test_hardening_cooldown_uses_injected_clock(monkeypatch):
    import types

    state = {"now": 100.0}
    fake = types.SimpleNamespace(config=types.SimpleNamespace(
        raw={"research": {"serp_hardening": True}}))
    monkeypatch.setitem(__import__("sys").modules, "config", fake)
    monkeypatch.setattr(sp, "_now", lambda: state["now"])

    sp._record_engine_failure("bing")
    sp._record_engine_failure("bing")
    assert sp._engine_available("bing") is False

    state["now"] += sp._ENGINE_COOLDOWN_SECONDS + 0.1
    assert sp._engine_available("bing") is True


def test_hardening_cache_ttl_uses_injected_clock(monkeypatch):
    import types

    state = {"now": 10.0}
    fake = types.SimpleNamespace(config=types.SimpleNamespace(
        raw={"research": {"serp_hardening": True}}))
    monkeypatch.setitem(__import__("sys").modules, "config", fake)
    monkeypatch.setattr(sp, "_now", lambda: state["now"])

    sp._cache_results("bing", "topic", "us-en", [{"url": "https://e.test", "title": "E", "snippet": ""}])
    assert sp._cached_results("bing", "topic", "us-en") == [
        {"url": "https://e.test", "title": "E", "snippet": ""}
    ]

    state["now"] += sp._RESULT_CACHE_TTL_SECONDS + 0.1
    assert sp._cached_results("bing", "topic", "us-en") is None
