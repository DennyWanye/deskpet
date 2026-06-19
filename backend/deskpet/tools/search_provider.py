# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Unified web-search provider — DuckDuckGo HTML SERP (no API key).

This is the **single** search implementation shared by:

* ``web_tools`` chat-mode ``web_search`` tool (quick lookups),
* ``code_tools.web_search_tool`` (Code-mode agent),
* ``research_tools.default_search`` (deep-research pipeline).

Before this module those three carried *three* near-identical DuckDuckGo
scrapers, each with the same bug: a hardcoded ``kl="us-en"`` region that
made Chinese queries return mostly English/irrelevant results. We fix
that here once: the region is **inferred from the query language** (CJK →
``cn-zh``, else ``us-en``) and overridable.

Design constraints (per project decision 2026-06-13):
* **No external/paid search engines.** DuckDuckGo's free HTML endpoint
  only. No Tavily/Exa/Bing/SearXNG. If DDG fails we return ``[]`` + an
  error string; callers degrade, they do not silently swap engines.
* Pure-parser path is unit-testable without network (``parse_ddg_html``).
* Synchronous (``search``) AND async (``search_async``) entry points so
  both the blocking Code tool and the async research pipeline reuse it.
"""
from __future__ import annotations

import logging
import re
import urllib.parse
from html import unescape
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

# ── 多引擎兼容性降级队列 ──────────────────────────────────────────────
# 现状痛点: 唯一引擎 DuckDuckGo 在中国大陆常被墙/不稳。改成按"中国区可达性"
# 排队的 fallback: 挨个试,第一个返回够结果的就用,失败/空/超时→降级下一个。
#   必应(bing)   = 中国可达 + 中文结果不错 + 免 key 抓取 → 默认主力
#   duckduckgo   = 西方引擎(墙内需代理) → 默认兜底
#   百度(baidu)  = 覆盖最高但广告/百家号多(已降权) → 备选,默认不在队列,可配置加入
_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
_BING_URL = "https://www.bing.com/search"
_BAIDU_URL = "https://www.baidu.com/s"

_DEFAULT_ENGINE_QUEUE = ("bing", "duckduckgo")  # 百度备选,不默认启用
_KNOWN_ENGINES = ("bing", "duckduckgo", "baidu")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)
_DEFAULT_TIMEOUT = 12.0
_MAX_RESULTS_CAP = 10


def _engine_queue() -> list[str]:
    """``[research].search_engines`` 配置的引擎降级队列;默认 (bing, duckduckgo)。
    只保留已知引擎,顺序即降级优先级。中国用户可配 ['baidu','bing'] 等。"""
    try:
        import config as _cfg  # type: ignore[import-not-found]
        v = (_cfg.config.raw.get("research") or {}).get("search_engines")
    except Exception:  # noqa: BLE001 — 配置不可用 → 默认队列
        v = None
    if isinstance(v, list) and v:
        q = [str(x).strip().lower() for x in v]
        q = [e for e in q if e in _KNOWN_ENGINES]
        if q:
            return q
    return list(_DEFAULT_ENGINE_QUEUE)


def _engine_request(engine: str, query: str, region: str):
    """→ (method, url, httpx_kwargs)。region: 'cn-zh'/'us-en'。"""
    if engine == "duckduckgo":
        return ("POST", _DDG_HTML_URL, {"data": {"q": query, "kl": region}})
    if engine == "bing":
        mkt = "zh-CN" if region == "cn-zh" else "en-US"
        return ("GET", _BING_URL, {"params": {"q": query, "mkt": mkt}})
    if engine == "baidu":
        return ("GET", _BAIDU_URL, {"params": {"wd": query}})
    return None


def _engine_parse(engine: str, html: str, max_results: int) -> list[dict[str, str]]:
    if engine == "duckduckgo":
        return parse_ddg_html(html, max_results=max_results)
    if engine == "bing":
        return parse_bing_html(html, max_results=max_results)
    if engine == "baidu":
        return parse_baidu_html(html, max_results=max_results)
    return []

_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_CJK_RE = re.compile(r"[㐀-鿿぀-ヿ가-힯]")


def region_for_query(query: str) -> str:
    """Infer the DuckDuckGo ``kl`` region from the query language.

    CJK (Chinese/Japanese/Korean) chars → ``cn-zh`` (中文区, far better
    Chinese coverage than the old hardcoded us-en). Otherwise ``us-en``.
    This is the single most impactful fix for Chinese deep-research.
    """
    return "cn-zh" if _CJK_RE.search(query or "") else "us-en"


def _strip(html: str) -> str:
    return unescape(_TAG_STRIP_RE.sub("", html)).strip()


def _clean_url(url: str) -> str:
    """DDG wraps targets in ``/l/?uddg=ENCODED``; unwrap to the real URL."""
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if "duckduckgo.com/l/" in url or url.startswith("/l/") or "uddg=" in url:
        try:
            parsed = urllib.parse.urlparse(url)
            qs = urllib.parse.parse_qs(parsed.query)
            uddg = qs.get("uddg", [""])[0]
            if uddg:
                return urllib.parse.unquote(uddg)
        except Exception:  # noqa: BLE001
            pass
    return url


def parse_ddg_html(html: str, *, max_results: int) -> list[dict[str, str]]:
    """Pure parser → ``[{url, title, snippet}]`` (network-free, testable).

    Prefers selectolax (robust to attribute order); falls back to regex.
    """
    out: list[dict[str, str]] = []
    try:
        from selectolax.parser import HTMLParser  # type: ignore
    except ImportError:
        return _parse_ddg_regex(html, max_results=max_results)

    tree = HTMLParser(html)
    for node in tree.css("div.result"):
        if len(out) >= max_results:
            break
        a = node.css_first("a.result__a")
        s = node.css_first(".result__snippet")
        if a is None:
            continue
        url = _clean_url(a.attributes.get("href", "") or "")
        title = (a.text() or "").strip()
        snippet = (s.text() if s else "").strip()
        if not url or not title:
            continue
        out.append({"url": url, "title": title, "snippet": snippet})
    if out:
        return out
    # selectolax present but layout changed → regex safety net.
    return _parse_ddg_regex(html, max_results=max_results)


def _parse_ddg_regex(html: str, *, max_results: int) -> list[dict[str, str]]:
    block = re.compile(
        r'<a class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
        r'(?:.*?<a class="result__snippet"[^>]*>(.*?)</a>)?',
        re.DOTALL,
    )
    out: list[dict[str, str]] = []
    for m in block.finditer(html):
        if len(out) >= max_results:
            break
        url = _clean_url(m.group(1))
        title = _strip(m.group(2) or "")
        snippet = _strip(m.group(3) or "")
        if not url or not title:
            continue
        out.append({"url": url, "title": title, "snippet": snippet})
    return out


def parse_bing_html(html: str, *, max_results: int) -> list[dict[str, str]]:
    """Bing SERP 解析 → ``[{url,title,snippet}]``。结果块 ``li.b_algo``:
    ``h2 a``(标题+真链,Bing 一般直链不包装) + ``.b_caption p``(摘要)。"""
    out: list[dict[str, str]] = []
    try:
        from selectolax.parser import HTMLParser  # type: ignore
    except ImportError:
        return out
    tree = HTMLParser(html)
    for node in tree.css("li.b_algo"):
        if len(out) >= max_results:
            break
        a = node.css_first("h2 a")
        if a is None:
            continue
        url = (a.attributes.get("href") or "").strip()
        title = (a.text() or "").strip()
        s = node.css_first(".b_caption p") or node.css_first("p")
        snippet = (s.text() if s else "").strip()
        if url.startswith("http") and title:
            out.append({"url": url, "title": title, "snippet": snippet})
    return out


def parse_baidu_html(html: str, *, max_results: int) -> list[dict[str, str]]:
    """百度 SERP 解析 → ``[{url,title,snippet}]``。结果块 ``div.result``/
    ``c-container``: ``h3 a``。百度把真链包在 ``/link?url=`` 跳转里,这里保留
    (web_fetch 跟随重定向自然解析到真链);抓不到/被反爬 → 空(降级下一引擎)。"""
    out: list[dict[str, str]] = []
    try:
        from selectolax.parser import HTMLParser  # type: ignore
    except ImportError:
        return out
    tree = HTMLParser(html)
    nodes = tree.css("div.result") or tree.css("div.c-container")
    for node in nodes:
        if len(out) >= max_results:
            break
        a = node.css_first("h3 a") or node.css_first("a")
        if a is None:
            continue
        url = (a.attributes.get("href") or "").strip()
        title = (a.text() or "").strip()
        s = node.css_first(".c-abstract") or node.css_first("[class*=abstract]")
        snippet = (s.text() if s else "").strip()
        if url.startswith("http") and title:
            out.append({"url": url, "title": title, "snippet": snippet})
    return out


def _normalize_max(max_results: int) -> int:
    try:
        n = int(max_results)
    except (TypeError, ValueError):
        n = 5
    return max(1, min(n, _MAX_RESULTS_CAP))


_HEADERS = {"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}


def search(
    query: str,
    *,
    max_results: int = 5,
    region: Optional[str] = None,
    timeout: float = _DEFAULT_TIMEOUT,
    engines: Optional[list[str]] = None,
) -> dict[str, Any]:
    """同步搜索(兼容性降级队列)→ ``{query, count, results, region, engine, [error]}``。

    按队列依次试引擎(默认 bing→duckduckgo,百度备选),第一个返回非空结果的就用;
    每引擎独立超时;全队列失败 → ``results: []`` + ``error``。Never raises。
    """
    q = (query or "").strip()
    if not q:
        return {"query": "", "count": 0, "results": [], "error": "empty query"}
    n = _normalize_max(max_results)
    reg = region or region_for_query(q)
    queue = engines or _engine_queue()
    last_err: Optional[str] = None
    for engine in queue:
        req = _engine_request(engine, q, reg)
        if req is None:
            continue
        method, url, kw = req
        try:
            with httpx.Client(headers=_HEADERS, timeout=timeout, follow_redirects=True) as client:
                resp = client.request(method, url, **kw)
            resp.raise_for_status()
            results = _engine_parse(engine, resp.text, n)
        except Exception as exc:  # noqa: BLE001
            last_err = f"{engine}: {exc}"
            log.debug("search engine %s failed for %r: %s", engine, q, exc)
            continue
        if results:
            return {"query": q, "count": len(results), "results": results,
                    "region": reg, "engine": engine}
    return {"query": q, "count": 0, "results": [], "region": reg,
            "error": last_err or "all engines returned empty"}


async def search_async(
    query: str,
    *,
    max_results: int = 5,
    region: Optional[str] = None,
    timeout: float = _DEFAULT_TIMEOUT,
    client: Optional[httpx.AsyncClient] = None,
    engines: Optional[list[str]] = None,
) -> list[dict[str, str]]:
    """异步搜索(兼容性降级队列)→ ``[{url, title, snippet}]``(research 管线形状)。
    按队列依次试,第一个非空就返回;全失败 → ``[]``。"""
    q = (query or "").strip()
    if not q:
        return []
    n = _normalize_max(max_results)
    reg = region or region_for_query(q)
    queue = engines or _engine_queue()
    owns = client is None
    cli = client or httpx.AsyncClient(headers=_HEADERS, timeout=timeout, follow_redirects=True)
    try:
        for engine in queue:
            req = _engine_request(engine, q, reg)
            if req is None:
                continue
            method, url, kw = req
            try:
                resp = await cli.request(method, url, **kw)
                resp.raise_for_status()
                results = _engine_parse(engine, resp.text, n)
            except Exception as exc:  # noqa: BLE001
                log.debug("async search engine %s failed for %r: %s", engine, q, exc)
                continue
            if results:
                return results
        return []
    finally:
        if owns:
            await cli.aclose()


__all__ = [
    "search", "search_async", "region_for_query",
    "parse_ddg_html", "parse_bing_html", "parse_baidu_html",
]
