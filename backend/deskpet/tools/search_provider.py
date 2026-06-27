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

import asyncio
import logging
import re
import time
import urllib.parse
from html import unescape
from typing import Any, Optional

import httpx

from .research_cdp_edge import cdp_edge_render

log = logging.getLogger(__name__)

# ── 多引擎降级队列 ──────────────────────────────────────────────
# §6.0(2026-06-20)起默认队列 = ("google-cdp",) —— Bing/DDG 裸 SERP 抓取易被 IP 封
# (Phase0 spike 实证),已**移出默认**;通用主题主靠直连源(百度/搜狗百科国内稳定 +
# 维基/谷歌可达门控,见 research_sources / research_tools §4.4)。下列引擎仍可经
# [research].search_engines 显式 opt-in 当兜底:
#   google-cdp   = 无头浏览器渲染谷歌 SERP + 可达门控(有VPN才用) → **默认队列唯一项**
#   bing/duckduckgo/baidu = 裸 SERP 抓取(易被封) → 仅 opt-in
#   bing-cdp     = 无头浏览器渲染必应 SERP → opt-in
#   searxng      = 自托管聚合(需 searxng_url) → opt-in
_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
_BING_URL = "https://www.bing.com/search"
_GOOGLE_HOME_URL = "https://www.google.com/"
_GOOGLE_SEARCH_URL = "https://www.google.com/search"
_BAIDU_URL = "https://www.baidu.com/s"

# §6.0 用户指令(2026-06-20): 不再默认用 Bing/DDG(裸 SERP 易被 IP 封)。默认只 google-cdp
# (无头浏览器渲染谷歌,可达门控:有 VPN/能访问才用,不通自动跳过)。通用主题主要靠直连源
# (百度百科/搜狗百科国内稳定 + 维基可达门控)。bing/duckduckgo/baidu/bing-cdp/searxng 仍
# 在 _KNOWN_ENGINES,用户可经 [research].search_engines 显式 opt-in 当兜底。
_DEFAULT_ENGINE_QUEUE = ("google-cdp",)
_KNOWN_ENGINES = ("bing", "duckduckgo", "baidu", "bing-cdp", "google-cdp", "searxng")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)
_DEFAULT_TIMEOUT = 12.0
_MAX_RESULTS_CAP = 10
_SEARCH_CDP_MAX_PER_RUN = 4
_SERP_RENDER_TIMEOUT = 8.0
_CAPTCHA_HTML_MIN_BYTES = 10 * 1024
_GOOGLE_REACHABLE_TIMEOUT = 4.0
_GOOGLE_REACHABLE_TTL_SECONDS = 60.0
_ENGINE_FAILURES_BEFORE_COOLDOWN = 2
_ENGINE_COOLDOWN_SECONDS = 5 * 60.0
_RESULT_CACHE_TTL_SECONDS = 2 * 60.0
_HARDENING_RETRIES = 2
_HARDENING_BACKOFF_SECONDS = 0.25

_BROWSER_UAS = (
    _UA,
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"
    ),
)
_BASE_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
_HARDENED_HEADER_BASE = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

_now = lambda: time.monotonic()
_sleep = time.sleep
_async_sleep = asyncio.sleep
_search_cdp_count = 0
_google_reachable_cache: Optional[tuple[float, bool]] = None
_engine_failures: dict[str, int] = {}
_engine_cooldown_until: dict[str, float] = {}
_result_cache: dict[tuple[str, str, str], tuple[float, list[dict[str, str]]]] = {}
_last_engines_hit: list[str] = []
_last_search_errors: list[str] = []


def _engine_queue() -> list[str]:
    """``[research].search_engines`` 配置的引擎降级队列;默认见 _DEFAULT_ENGINE_QUEUE。
    只保留已知引擎,顺序即降级优先级。"""
    v = _research_raw().get("search_engines")
    if isinstance(v, list) and v:
        q = [str(x).strip().lower() for x in v]
        q = [e for e in q if e in _KNOWN_ENGINES]
        if "searxng" in q and not _searxng_url():
            q = [e for e in q if e != "searxng"]
        if q:
            return q
    return list(_DEFAULT_ENGINE_QUEUE)


def _research_raw() -> dict[str, Any]:
    """返回 config.toml 的 ``[research]`` 段 dict;读不到返回 {}。

    ⚠️ 2026-06-21 真机 E2E 修复:原实现只读 ``config.config.raw``,但 ``config.config``
    单例并不存在(config.py 无该模块全局、main.py 不注入)→ 恒 AttributeError → 所有
    [research] 开关(search_engines/searxng_url/serp_hardening)读不到 config.toml、全失效。
    改用 research_tools._research_raw 同款健壮兜底:有发布单例则用,否则 load_config(
    resolve_config_path()) 直读真实 config 文件。
    """
    try:
        import config as _cfg  # type: ignore[import-not-found]
        obj = getattr(_cfg, "config", None)  # 若有发布的单例优先
        if obj is not None and hasattr(obj, "raw"):
            raw = obj.raw.get("research") or {}
        else:
            cfg = _cfg.load_config(_cfg.resolve_config_path())
            raw = cfg.raw.get("research") or {}
        return raw if isinstance(raw, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _searxng_url() -> str:
    v = _research_raw().get("searxng_url", "")
    return str(v or "").strip().rstrip("/")


def _serp_hardening() -> bool:
    return bool(_research_raw().get("serp_hardening", False))


def _serp_render_timeout() -> float:
    try:
        return float(_research_raw().get("serp_render_timeout", _SERP_RENDER_TIMEOUT) or _SERP_RENDER_TIMEOUT)
    except (TypeError, ValueError):
        return _SERP_RENDER_TIMEOUT


def reset_search_cdp_budget() -> None:
    """Reset the per-run search-CDP budget counter.

    ``research_tools`` can call this once at the start of each deepresearch
    run; search CDP shares Edge's render semaphore but keeps an independent
    count from fetch-stage JS rendering.
    """
    global _search_cdp_count
    _search_cdp_count = 0


def reset_search_runtime_state() -> None:
    """Clear process-local search hardening state for tests or a new runtime."""
    reset_google_reachable_cache()
    _engine_failures.clear()
    _engine_cooldown_until.clear()
    _result_cache.clear()
    _last_engines_hit.clear()
    _last_search_errors.clear()


def reset_google_reachable_cache() -> None:
    """Clear the short-lived Google reachability probe cache."""
    global _google_reachable_cache
    _google_reachable_cache = None


def get_last_engines_hit() -> list[str]:
    """Return engine names that produced the latest search result set.

    ``search_async`` returns only the legacy list of result dicts, so route
    observability reads this side-channel immediately after a call. The list is
    reset at the beginning of each ``search``/``search_async`` invocation.
    """
    return list(_last_engines_hit)


def get_last_search_errors() -> list[str]:
    """Return explicit errors captured during the latest search invocation."""
    return list(_last_search_errors)


def _reset_last_observation() -> None:
    _last_engines_hit.clear()
    _last_search_errors.clear()


def _mark_engine_hit(engine: str) -> None:
    if engine not in _last_engines_hit:
        _last_engines_hit.append(engine)


def _record_search_error(error: str) -> None:
    if error and error not in _last_search_errors:
        _last_search_errors.append(error)


def _engine_available(engine: str) -> bool:
    if not _serp_hardening():
        return True
    until = _engine_cooldown_until.get(engine, 0.0)
    if until and _now() < until:
        return False
    if until:
        _engine_cooldown_until.pop(engine, None)
        _engine_failures[engine] = 0
    return True


def _record_engine_failure(engine: str) -> None:
    if not _serp_hardening():
        return
    failures = _engine_failures.get(engine, 0) + 1
    _engine_failures[engine] = failures
    if failures >= _ENGINE_FAILURES_BEFORE_COOLDOWN:
        _engine_cooldown_until[engine] = _now() + _ENGINE_COOLDOWN_SECONDS


def _record_engine_success(engine: str) -> None:
    if not _serp_hardening():
        return
    _engine_failures[engine] = 0
    _engine_cooldown_until.pop(engine, None)


def _cache_key(engine: str, query: str, region: str) -> tuple[str, str, str]:
    return (engine, query.strip().lower(), region)


def _cached_results(engine: str, query: str, region: str) -> Optional[list[dict[str, str]]]:
    if not _serp_hardening():
        return None
    item = _result_cache.get(_cache_key(engine, query, region))
    if item is None:
        return None
    expires_at, results = item
    if _now() >= expires_at:
        _result_cache.pop(_cache_key(engine, query, region), None)
        return None
    return [dict(r) for r in results]


def _cache_results(engine: str, query: str, region: str, results: list[dict[str, str]]) -> None:
    if not _serp_hardening() or not results:
        return
    _result_cache[_cache_key(engine, query, region)] = (
        _now() + _RESULT_CACHE_TTL_SECONDS,
        [dict(r) for r in results],
    )


def _headers_for_request(engine: str, query: str, *, hardened: bool) -> dict[str, str]:
    if not hardened:
        return dict(_HEADERS)
    idx = abs(hash((engine, query, int(_now() // 60)))) % len(_BROWSER_UAS)
    headers = dict(_HARDENED_HEADER_BASE)
    headers["User-Agent"] = _BROWSER_UAS[idx]
    return headers


def _backoff_delay(attempt: int) -> float:
    return _HARDENING_BACKOFF_SECONDS * (2 ** max(0, attempt))


def _bing_serp_url(query: str, region: str) -> str:
    mkt = "zh-CN" if region == "cn-zh" else "en-US"
    return _BING_URL + "?" + urllib.parse.urlencode({"q": query, "mkt": mkt})


def _google_serp_url(query: str, region: str) -> str:
    hl = "zh-CN" if region == "cn-zh" else "en"
    return f"{_GOOGLE_SEARCH_URL}?q={urllib.parse.quote(query, safe='')}&hl={hl}"


def _google_reachable() -> bool:
    """Lightweight Google availability gate with a short process-local TTL."""
    global _google_reachable_cache
    now = _now()
    if _google_reachable_cache is not None:
        expires_at, reachable = _google_reachable_cache
        if now < expires_at:
            return reachable

    reachable = False
    try:
        with httpx.Client(
            headers=_BASE_HEADERS,
            timeout=_GOOGLE_REACHABLE_TIMEOUT,
            follow_redirects=True,
        ) as client:
            resp = client.get(_GOOGLE_HOME_URL)
        reachable = int(getattr(resp, "status_code", 200)) < 500
    except Exception as exc:  # noqa: BLE001
        log.debug("google reachability probe failed: %s", exc)

    _google_reachable_cache = (now + _GOOGLE_REACHABLE_TTL_SECONDS, reachable)
    return reachable


def _search_cdp_budget_available() -> bool:
    return _search_cdp_count < _SEARCH_CDP_MAX_PER_RUN


def _consume_search_cdp_budget() -> bool:
    global _search_cdp_count
    if not _search_cdp_budget_available():
        return False
    _search_cdp_count += 1
    return True


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


def _parse_searxng_json(body: Any, max_results: int) -> list[dict[str, str]]:
    """Parse SearXNG JSON ``results`` into the unified result shape."""
    if not isinstance(body, dict):
        return []
    items = body.get("results")
    if not isinstance(items, list):
        return []
    out: list[dict[str, str]] = []
    for item in items:
        if len(out) >= max_results:
            break
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        snippet = str(item.get("content") or item.get("snippet") or "").strip()
        if url.startswith("http") and title:
            out.append({"url": url, "title": title, "snippet": snippet})
    return out


def _looks_like_bing_captcha(html: str, results: list[dict[str, str]]) -> bool:
    if results or not html or len(html.encode("utf-8", "ignore")) <= _CAPTCHA_HTML_MIN_BYTES:
        return False
    try:
        from selectolax.parser import HTMLParser  # type: ignore
        if HTMLParser(html).css_first("li.b_algo") is not None:
            return False
    except ImportError:
        if re.search(r"<li\b[^>]*class=[\"'][^\"']*\bb_algo\b", html, flags=re.I):
            return False
    except Exception:  # noqa: BLE001
        pass
    if "li.b_algo" in html:
        return False
    text = _strip(html).lower()
    needles = ("verify", "unusual traffic", "captcha", "机器人")
    return any(n in text for n in needles)


def _clean_google_url(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.path == "/url":
            qs = urllib.parse.parse_qs(parsed.query)
            target = qs.get("q", [""])[0] or qs.get("url", [""])[0]
            if target:
                return urllib.parse.unquote(target)
    except Exception:  # noqa: BLE001
        return url
    return url


def _node_text(node: Any) -> str:
    return re.sub(r"\s+", " ", (node.text() or "")).strip()


def _parse_google_html(html: str, max_results: int) -> list[dict[str, str]]:
    """Parse Google organic SERP HTML rendered by CDP."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    try:
        from selectolax.parser import HTMLParser  # type: ignore
    except ImportError:
        return out

    tree = HTMLParser(html or "")
    containers = list(tree.css("div.g")) + list(tree.css("div[data-hveid]"))
    for node in containers:
        if len(out) >= max_results:
            break
        a_node = None
        h3_node = None
        for a in node.css("a[href]"):
            h3 = a.css_first("h3")
            if h3 is not None:
                a_node = a
                h3_node = h3
                break
        if a_node is None or h3_node is None:
            continue

        url = _clean_google_url((a_node.attributes.get("href") or "").strip())
        title = _node_text(h3_node)
        if not url.startswith("http") or not title or url in seen:
            continue

        snippet = ""
        for selector in (".VwiC3b", "[data-sncf]", "[data-content-feature]"):
            s = node.css_first(selector)
            if s is not None:
                snippet = _node_text(s)
                break
        if not snippet:
            text = _node_text(node)
            snippet = text.replace(title, "", 1).strip()
        out.append({"url": url, "title": title, "snippet": snippet})
        seen.add(url)
    return out


def _looks_like_google_captcha(html: str, results: list[dict[str, str]]) -> bool:
    if results or not html or len(html.encode("utf-8", "ignore")) <= _CAPTCHA_HTML_MIN_BYTES:
        return False
    text = html.lower()
    needles = ("recaptcha", "g-recaptcha", "unusual traffic", "/sorry/")
    return any(n in text for n in needles)


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


_HEADERS = dict(_BASE_HEADERS)


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
    _reset_last_observation()
    if not q:
        return {"query": "", "count": 0, "results": [], "error": "empty query"}
    n = _normalize_max(max_results)
    reg = region or region_for_query(q)
    queue = engines or _engine_queue()
    last_err: Optional[str] = None
    for engine in queue:
        if engine in {"bing-cdp", "google-cdp", "searxng"}:
            continue
        if not _engine_available(engine):
            last_err = f"{engine}: cooling down"
            _record_search_error(last_err)
            continue
        cached = _cached_results(engine, q, reg)
        if cached is not None:
            _mark_engine_hit(engine)
            return {"query": q, "count": len(cached), "results": cached,
                    "region": reg, "engine": engine}
        req = _engine_request(engine, q, reg)
        if req is None:
            continue
        method, url, kw = req
        attempts = _HARDENING_RETRIES if _serp_hardening() else 1
        results: list[dict[str, str]] = []
        try:
            for attempt in range(attempts):
                headers = _headers_for_request(engine, q, hardened=_serp_hardening())
                try:
                    with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
                        resp = client.request(method, url, **kw)
                    resp.raise_for_status()
                    results = _engine_parse(engine, resp.text, n)
                    break
                except Exception:
                    if attempt + 1 >= attempts:
                        raise
                    _sleep(_backoff_delay(attempt))
                    continue
        except Exception as exc:  # noqa: BLE001
            last_err = f"{engine}: {exc}"
            _record_engine_failure(engine)
            _record_search_error(last_err)
            log.debug("search engine %s failed for %r: %s", engine, q, exc)
            continue
        if results:
            _record_engine_success(engine)
            _cache_results(engine, q, reg, results)
            _mark_engine_hit(engine)
            return {"query": q, "count": len(results), "results": results,
                    "region": reg, "engine": engine}
        _record_engine_failure(engine)
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
    _reset_last_observation()
    if not q:
        return []
    n = _normalize_max(max_results)
    reg = region or region_for_query(q)
    queue = engines or _engine_queue()
    owns = client is None
    cli = client
    try:
        for engine in queue:
            if not _engine_available(engine):
                _record_search_error(f"{engine}: cooling down")
                continue
            cached = _cached_results(engine, q, reg)
            if cached is not None:
                _mark_engine_hit(engine)
                return cached
            if engine == "bing-cdp":
                if not _consume_search_cdp_budget():
                    _record_search_error("bing-cdp: budget exhausted")
                    continue
                try:
                    html = await cdp_edge_render(_bing_serp_url(q, reg), timeout=_serp_render_timeout())
                    if html is None:
                        _record_engine_failure(engine)
                        _record_search_error("bing-cdp: render returned none")
                        continue
                    results = parse_bing_html(html, max_results=n)
                    if _looks_like_bing_captcha(html, results):
                        _record_engine_failure(engine)
                        _record_search_error("bing_cdp_captcha_suspected")
                        log.warning("bing_cdp_captcha_suspected", extra={"query": q})
                        continue
                except Exception as exc:  # noqa: BLE001
                    _record_engine_failure(engine)
                    _record_search_error(f"{engine}: {exc}")
                    log.debug("async search engine %s failed for %r: %s", engine, q, exc)
                    continue
                if results:
                    _record_engine_success(engine)
                    _cache_results(engine, q, reg, results)
                    _mark_engine_hit(engine)
                    return results
                _record_engine_failure(engine)
                continue

            if engine == "google-cdp":
                if not _google_reachable():
                    _record_engine_failure(engine)
                    _record_search_error("google-cdp: unreachable")
                    continue
                if not _consume_search_cdp_budget():
                    _record_search_error("google-cdp: budget exhausted")
                    continue
                try:
                    html = await cdp_edge_render(_google_serp_url(q, reg), timeout=_serp_render_timeout())
                    if html is None:
                        _record_engine_failure(engine)
                        _record_search_error("google-cdp: render returned none")
                        continue
                    results = _parse_google_html(html, max_results=n)
                    if _looks_like_google_captcha(html, results):
                        _record_engine_failure(engine)
                        _record_search_error("google_cdp_captcha_suspected")
                        log.warning("google_cdp_captcha_suspected", extra={"query": q})
                        continue
                except Exception as exc:  # noqa: BLE001
                    _record_engine_failure(engine)
                    _record_search_error(f"{engine}: {exc}")
                    log.debug("async search engine %s failed for %r: %s", engine, q, exc)
                    continue
                if results:
                    _record_engine_success(engine)
                    _cache_results(engine, q, reg, results)
                    _mark_engine_hit(engine)
                    return results
                _record_engine_failure(engine)
                continue

            if engine == "searxng":
                searxng_url = _searxng_url()
                if not searxng_url:
                    _record_search_error("searxng: missing searxng_url")
                    continue
                attempts = _HARDENING_RETRIES if _serp_hardening() else 1
                results = []
                try:
                    if cli is None:
                        cli = httpx.AsyncClient(
                            headers=_headers_for_request(engine, q, hardened=_serp_hardening()),
                            timeout=timeout,
                            follow_redirects=True,
                        )
                    for attempt in range(attempts):
                        try:
                            resp = await cli.request(
                                "GET",
                                searxng_url,
                                params={"q": q, "format": "json"},
                            )
                            resp.raise_for_status()
                            results = _parse_searxng_json(resp.json(), n)
                            break
                        except Exception:
                            if attempt + 1 >= attempts:
                                raise
                            await _async_sleep(_backoff_delay(attempt))
                            continue
                except Exception as exc:  # noqa: BLE001
                    _record_engine_failure(engine)
                    _record_search_error(f"{engine}: {exc}")
                    log.debug("async search engine %s failed for %r: %s", engine, q, exc)
                    continue
                if results:
                    _record_engine_success(engine)
                    _cache_results(engine, q, reg, results)
                    _mark_engine_hit(engine)
                    return results
                _record_engine_failure(engine)
                continue

            req = _engine_request(engine, q, reg)
            if req is None:
                continue
            method, url, kw = req
            attempts = _HARDENING_RETRIES if _serp_hardening() else 1
            results = []
            try:
                if cli is None:
                    cli = httpx.AsyncClient(
                        headers=_headers_for_request(engine, q, hardened=_serp_hardening()),
                        timeout=timeout,
                        follow_redirects=True,
                    )
                for attempt in range(attempts):
                    try:
                        resp = await cli.request(method, url, **kw)
                        resp.raise_for_status()
                        results = _engine_parse(engine, resp.text, n)
                        break
                    except Exception:
                        if attempt + 1 >= attempts:
                            raise
                        await _async_sleep(_backoff_delay(attempt))
                        continue
            except Exception as exc:  # noqa: BLE001
                _record_engine_failure(engine)
                _record_search_error(f"{engine}: {exc}")
                log.debug("async search engine %s failed for %r: %s", engine, q, exc)
                continue
            if results:
                _record_engine_success(engine)
                _cache_results(engine, q, reg, results)
                _mark_engine_hit(engine)
                return results
            _record_engine_failure(engine)
        return []
    finally:
        if owns and cli is not None:
            await cli.aclose()


__all__ = [
    "search", "search_async", "region_for_query",
    "parse_ddg_html", "parse_bing_html", "parse_baidu_html",
    "get_last_engines_hit", "get_last_search_errors",
    "reset_search_cdp_budget", "reset_search_runtime_state",
    "reset_google_reachable_cache",
]
