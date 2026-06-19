# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Deep-research V8 — unified search_provider (region-aware DDG)."""
from __future__ import annotations

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
    assert sp._engine_queue() == ["bing", "duckduckgo"]  # 无配置 → 默认


def test_engine_queue_config(monkeypatch):
    import types
    fake = types.SimpleNamespace(config=types.SimpleNamespace(
        raw={"research": {"search_engines": ["baidu", "BING", "garbage"]}}))
    monkeypatch.setitem(__import__("sys").modules, "config", fake)
    assert sp._engine_queue() == ["baidu", "bing"]  # 清洗+小写+去非法,保序


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
    r = sp.search("测试", max_results=5)  # 默认 [bing, duckduckgo]
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
