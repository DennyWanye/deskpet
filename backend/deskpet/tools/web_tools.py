"""P4-S5: zero-cost web toolset (toolset=web).

Four tools, all built on httpx + trafilatura + selectolax — no paid
search APIs (D5 decision, enforced by CI grep guard task 9.9).

* ``web_fetch(url)`` — single-page HTTP GET → structured payload
  (status, content, content_type). HTML > 2MB gets truncated.
* ``web_crawl(seed_url, ...)`` — BFS same-origin crawl with robots.txt
  + rate limiting + keyword scoring.
* ``web_extract_article(url_or_html)`` — trafilatura structured
  extraction with selectolax fallback for title/text.
* ``web_read_sitemap(sitemap_url)`` — XML sitemap parser with
  sitemap_index fallback + recursive unfurl.

Shared machinery (robots cache + per-host rate limiter) lives at the
top of this file so every tool uses the same politeness layer.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.parse
import urllib.robotparser
from collections import deque
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from ._config import WebToolsConfig, load_web_config
from .registry import registry

# Re-export ``WebToolsConfig`` so tests can build disposable config
# instances via ``web_tools.WebToolsConfig(...)`` without having to
# know the ``_config`` submodule.
__all__ = ["WebToolsConfig", "load_web_config"]

logger = logging.getLogger(__name__)

_MAX_HTML_BYTES = 2 * 1024 * 1024  # 2 MB
_MAX_REDIRECTS = 5

# ---------------------------------------------------------------------
# Shared politeness state
# ---------------------------------------------------------------------
# Per-host monotonic last-fetch timestamp (seconds). Enforces the
# [tools.web].request_interval_ms floor. Not persisted across process
# restarts — a restart would honour rate limits from scratch anyway.
_last_fetch_ts: dict[str, float] = {}
_rate_lock = threading.Lock()

# Cached robots.txt parsers per host. We never refresh inside a process
# because most crawl sessions are short-lived; if this grows into an
# issue, add a TTL.
_robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}
_robots_lock = threading.Lock()

# Blocked-domain cache (req 9.7 / web-tools spec "Graceful Degradation").
# Keyed by host → (block_until_epoch, consecutive_block_count). When the
# current time < block_until_epoch, skip. We reset consecutive count on
# a successful fetch (200-299) so flaky but recovering sites don't stay
# blacklisted forever.
_block_cache: dict[str, tuple[float, int]] = {}
_block_lock = threading.Lock()
_BLOCK_THRESHOLD = 3  # consecutive blocks before cooldown kicks in
_BLOCK_COOLDOWN_S = 3600  # 1h


def _err(msg: str, retriable: bool = False) -> str:
    return json.dumps({"error": msg, "retriable": retriable}, ensure_ascii=False)


def _host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()


def _user_agent() -> str:
    return load_web_config().user_agent


def _client_headers() -> dict[str, str]:
    return {
        "User-Agent": _user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh;q=0.8",
    }


def _respect_robots(url: str) -> bool:
    cfg = load_web_config()
    if not cfg.respect_robots_txt:
        return True
    host = _host(url)
    if not host:
        return True
    with _robots_lock:
        rp = _robots_cache.get(host)
        if host not in _robots_cache:
            parsed = urllib.parse.urlparse(url)
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(robots_url)
            try:
                # Use our own UA string so sites that return different
                # robots.txt per-UA see what we actually send.
                with httpx.Client(
                    headers=_client_headers(),
                    timeout=5.0,
                    follow_redirects=True,
                ) as client:
                    resp = client.get(robots_url)
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                else:
                    # No robots.txt (or 4xx/5xx) → permissive by default,
                    # mirroring urllib.robotparser's empty-file behaviour.
                    rp.parse([])
            except Exception as exc:  # noqa: BLE001 — robots shouldn't break fetches
                logger.debug("robots fetch failed for %s: %s", host, exc)
                rp.parse([])
            _robots_cache[host] = rp
        else:
            rp = _robots_cache[host]
    if rp is None:
        return True
    return rp.can_fetch(_user_agent(), url)


def _throttle(host: str) -> None:
    cfg = load_web_config()
    interval = cfg.request_interval_ms / 1000.0
    while True:
        with _rate_lock:
            now = time.monotonic()
            last = _last_fetch_ts.get(host, 0.0)
            wait = interval - (now - last)
            if wait <= 0:
                _last_fetch_ts[host] = now
                return
        time.sleep(wait)


def _check_block(host: str) -> tuple[bool, str | None]:
    """Return ``(allowed, message)``. Allowed=False means caller should
    skip with an error. Entries with ``until=0.0`` are "counting but not
    yet blocked" — never treat them as blocked, and never delete them
    here (deletion would reset consecutive-block counting)."""
    with _block_lock:
        entry = _block_cache.get(host)
        if entry is None:
            return True, None
        until, _count = entry
        if until <= 0.0:
            # Still counting; not yet blocked.
            return True, None
        if time.time() < until:
            return False, "domain temporarily blocked"
        # Cooldown expired — drop the entry, give the domain another chance.
        del _block_cache[host]
    return True, None


def _register_block(host: str) -> None:
    with _block_lock:
        entry = _block_cache.get(host)
        count = (entry[1] + 1) if entry else 1
        if count >= _BLOCK_THRESHOLD:
            _block_cache[host] = (time.time() + _BLOCK_COOLDOWN_S, count)
        else:
            # Keep counting but don't actually block yet. ``until=0.0``
            # is the sentinel "still counting" state — ``_check_block``
            # treats it as allowed, not blocked.
            _block_cache[host] = (0.0, count)


def _clear_block(host: str) -> None:
    with _block_lock:
        _block_cache.pop(host, None)


def _detect_captcha(text: str) -> bool:
    needles = (
        "captcha",
        "cf-chl-bypass",
        "cloudflare",
        "are you a human",
        "security check",
    )
    lowered = text[:4000].lower()
    return any(n in lowered for n in needles)


# ---------------------------------------------------------------------
# web_fetch
# ---------------------------------------------------------------------
_SCHEMA_FETCH: dict[str, Any] = {
    "name": "web_fetch",
    "description": (
        "HTTP GET a URL and return status + content + content_type. "
        "Follows up to 5 redirects. HTML truncated to 2MB. Respects "
        "robots.txt and per-domain rate limits from config."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute http(s) URL."},
            "timeout": {
                "type": "integer",
                "description": "Per-request timeout seconds. Default 10.",
                "default": 10,
            },
        },
        "required": ["url"],
    },
}


def _fetch_one(url: str, timeout: float) -> dict[str, Any]:
    """Low-level fetch helper used by both web_fetch and web_crawl.

    Returns a dict with either ``{"status", "content", "content_type",
    "url_final"}`` on success or ``{"error", "retriable"}`` on failure.
    Does NOT emit JSON — callers wrap.
    """
    host = _host(url)
    if not host:
        return {"error": "invalid url (no host)", "retriable": False}
    allowed, msg = _check_block(host)
    if not allowed:
        return {"error": msg or "domain blocked", "retriable": True}
    if not _respect_robots(url):
        return {"error": "blocked by robots.txt", "retriable": False}
    _throttle(host)
    try:
        with httpx.Client(
            headers=_client_headers(),
            timeout=timeout,
            follow_redirects=True,
            max_redirects=_MAX_REDIRECTS,
        ) as client:
            resp = client.get(url)
    except httpx.TimeoutException:
        return {"error": "timeout", "retriable": True}
    except httpx.HTTPError as exc:
        return {"error": f"HTTPError: {exc}", "retriable": True}
    status = resp.status_code
    content_type = resp.headers.get("content-type", "")
    if status in (403, 429) or (
        "text/html" in content_type and _detect_captcha(resp.text)
    ):
        _register_block(host)
        return {
            "error": f"blocked (status={status})",
            "retriable": True,
            "status": status,
        }
    if 200 <= status < 300:
        _clear_block(host)
    # Truncate large HTML to keep agent prompts bounded.
    body = resp.text
    if "text/html" in content_type and len(body.encode("utf-8", "replace")) > _MAX_HTML_BYTES:
        body = body.encode("utf-8", "replace")[:_MAX_HTML_BYTES].decode(
            "utf-8", errors="replace"
        )
    return {
        "status": status,
        "content": body,
        "content_type": content_type,
        "url_final": str(resp.url),
    }


def _handle_web_fetch(args: dict[str, Any], task_id: str) -> str:
    url = str(args.get("url", "") or "").strip()
    if not url:
        return _err("url is required", retriable=False)
    timeout = float(args.get("timeout", 10) or 10)
    if timeout <= 0:
        return _err("timeout must be positive", retriable=False)
    result = _fetch_one(url, timeout)
    if "error" in result:
        payload = {"error": result["error"], "retriable": result["retriable"]}
        if "status" in result:
            payload["status"] = result["status"]
        return json.dumps(payload, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


registry.register("web_fetch", "web", _SCHEMA_FETCH, _handle_web_fetch)


# ---------------------------------------------------------------------
# web_extract_article
# ---------------------------------------------------------------------
_SCHEMA_ARTICLE: dict[str, Any] = {
    "name": "web_extract_article",
    "description": (
        "Extract structured article fields (title/author/date/text/"
        "language) from a URL or raw HTML using trafilatura. Falls back "
        "to selectolax for title+text if metadata extraction fails."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url_or_html": {
                "type": "string",
                "description": "Either an absolute http(s) URL or raw HTML text.",
            },
        },
        "required": ["url_or_html"],
    },
}


def _looks_like_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def _selectolax_fallback(html: str) -> dict[str, Any]:
    """Best-effort title/text extraction when trafilatura returns None.

    Returns what we could get with remaining keys as None so the caller
    always has the full 5-field shape.
    """
    title: str | None = None
    text: str | None = None
    try:
        from selectolax.parser import HTMLParser  # type: ignore

        parser = HTMLParser(html)
        t = parser.css_first("title")
        if t is not None:
            title = (t.text() or "").strip() or None
        body = parser.body
        if body is not None:
            # Strip script/style before extracting text.
            for sel in ("script", "style", "noscript"):
                for node in body.css(sel):
                    node.decompose()
            raw = body.text(separator="\n").strip()
            # Collapse excessive blank lines.
            text = re.sub(r"\n{3,}", "\n\n", raw) or None
    except Exception as exc:  # noqa: BLE001
        logger.debug("selectolax fallback failed: %s", exc)
    return {"title": title, "text": text}


def _handle_web_extract_article(args: dict[str, Any], task_id: str) -> str:
    inp = str(args.get("url_or_html", "") or "").strip()
    if not inp:
        return _err("url_or_html is required", retriable=False)

    if _looks_like_url(inp):
        fetched = _fetch_one(inp, timeout=10.0)
        if "error" in fetched:
            payload = {
                "error": fetched["error"],
                "retriable": fetched["retriable"],
            }
            return json.dumps(payload, ensure_ascii=False)
        html = fetched.get("content", "") or ""
    else:
        html = inp

    try:
        import trafilatura  # type: ignore

        # Prefer JSON output — newer trafilatura versions keep the
        # metadata in the same blob as the text.
        extracted_json = trafilatura.extract(
            html,
            output_format="json",
            with_metadata=True,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
        payload: dict[str, Any] = {
            "title": None,
            "author": None,
            "date": None,
            "text": None,
            "language": None,
        }
        if extracted_json:
            try:
                data = json.loads(extracted_json)
                # trafilatura's keys are stable; still guard with .get
                payload["title"] = data.get("title") or None
                payload["author"] = data.get("author") or None
                # Dates come through as ISO strings when present.
                payload["date"] = (
                    data.get("date")
                    or data.get("publication_date")
                    or None
                )
                payload["text"] = data.get("text") or data.get("raw_text") or None
                payload["language"] = data.get("language") or None
            except json.JSONDecodeError:
                pass
        if not payload["text"]:
            # Plain-text fallback on trafilatura.extract (older API).
            plain = trafilatura.extract(html, favor_precision=True)
            if plain:
                payload["text"] = plain
        if not payload["text"] or not payload["title"]:
            fb = _selectolax_fallback(html)
            if not payload["title"] and fb.get("title"):
                payload["title"] = fb["title"]
            if not payload["text"] and fb.get("text"):
                payload["text"] = fb["text"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("trafilatura extract failed: %s", exc)
        payload = {
            "title": None,
            "author": None,
            "date": None,
            "text": None,
            "language": None,
        }
        fb = _selectolax_fallback(html)
        payload["title"] = fb.get("title")
        payload["text"] = fb.get("text")

    return json.dumps(payload, ensure_ascii=False)


registry.register(
    "web_extract_article", "web", _SCHEMA_ARTICLE, _handle_web_extract_article
)


# ---------------------------------------------------------------------
# web_crawl
# ---------------------------------------------------------------------
_SCHEMA_CRAWL: dict[str, Any] = {
    "name": "web_crawl",
    "description": (
        "BFS same-origin crawl from a seed URL. Respects robots.txt + "
        "per-domain rate limit. Returns top-ranked pages by keyword "
        "frequency in title + body."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "start_url": {
                "type": "string",
                "description": "Absolute http(s) seed URL.",
            },
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Keywords to score pages by. Default [].",
            },
            "max_depth": {
                "type": "integer",
                "description": "BFS depth cap. Default 2.",
                "default": 2,
            },
            "max_pages": {
                "type": "integer",
                "description": "Page cap. Default 20.",
                "default": 20,
            },
            "same_origin": {
                "type": "boolean",
                "description": "Confine to seed's scheme+host. Default true.",
                "default": True,
            },
        },
        "required": ["start_url"],
    },
}


_LINK_RE = re.compile(
    r"""href\s*=\s*["']([^"'#]+)""", re.IGNORECASE
)


def _score_page(html: str, keywords: list[str]) -> tuple[float, str, str]:
    """Return ``(score, title, excerpt)``. Uses selectolax when available
    for a cleaner title; falls back to a regex tag strip for text."""
    title = ""
    text = ""
    try:
        from selectolax.parser import HTMLParser  # type: ignore

        parser = HTMLParser(html)
        t = parser.css_first("title")
        if t is not None:
            title = (t.text() or "").strip()
        body = parser.body
        if body is not None:
            for sel in ("script", "style", "noscript"):
                for node in body.css(sel):
                    node.decompose()
            text = (body.text(separator=" ") or "").strip()
    except Exception:  # noqa: BLE001
        text = re.sub(r"<[^>]+>", " ", html)

    haystack = (title + " " + text).lower()
    if not keywords:
        score = 1.0 if haystack.strip() else 0.0
    else:
        score = 0.0
        for kw in keywords:
            kw_l = kw.lower().strip()
            if not kw_l:
                continue
            # Title hits weight 3x, body hits weight 1x.
            score += 3 * title.lower().count(kw_l)
            score += text.lower().count(kw_l)
    excerpt = text[:300].replace("\n", " ").strip()
    return score, title, excerpt


def _extract_links(base_url: str, html: str) -> list[str]:
    links: list[str] = []
    for m in _LINK_RE.finditer(html):
        raw = m.group(1).strip()
        if not raw or raw.startswith(("mailto:", "javascript:", "tel:")):
            continue
        try:
            joined = urllib.parse.urljoin(base_url, raw)
        except ValueError:
            continue
        # Normalize — drop fragments, keep query.
        parsed = urllib.parse.urlparse(joined)
        if parsed.scheme not in ("http", "https"):
            continue
        cleaned = urllib.parse.urlunparse(parsed._replace(fragment=""))
        links.append(cleaned)
    # Dedup while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for link in links:
        if link in seen:
            continue
        seen.add(link)
        out.append(link)
    return out


def _handle_web_crawl(args: dict[str, Any], task_id: str) -> str:
    start_url = str(args.get("start_url", "") or "").strip()
    if not start_url:
        return _err("start_url is required", retriable=False)
    keywords = list(args.get("keywords") or [])
    if not isinstance(keywords, list):
        return _err("keywords must be a list", retriable=False)
    cfg = load_web_config()
    max_depth = int(args.get("max_depth", cfg.crawl_default_max_depth) or cfg.crawl_default_max_depth)
    max_pages = int(args.get("max_pages", cfg.crawl_default_max_pages) or cfg.crawl_default_max_pages)
    same_origin = bool(args.get("same_origin", True))
    if max_depth < 0 or max_pages <= 0:
        return _err("max_depth>=0 and max_pages>0 required", retriable=False)

    seed = urllib.parse.urlparse(start_url)
    if seed.scheme not in ("http", "https") or not seed.hostname:
        return _err("start_url must be an absolute http(s) URL", retriable=False)
    seed_origin = f"{seed.scheme}://{seed.hostname.lower()}"

    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(start_url, 0)])
    pages: list[dict[str, Any]] = []

    while queue and len(pages) < max_pages:
        url, depth = queue.popleft()
        if url in visited:
            continue
        visited.add(url)
        if same_origin:
            parsed = urllib.parse.urlparse(url)
            if (
                parsed.scheme != seed.scheme
                or (parsed.hostname or "").lower() != seed.hostname.lower()
            ):
                continue
        fetched = _fetch_one(url, timeout=10.0)
        if "error" in fetched:
            # Keep going — a single failed URL shouldn't kill the crawl.
            logger.debug("crawl skip %s: %s", url, fetched["error"])
            continue
        content_type = fetched.get("content_type", "") or ""
        if "text/html" not in content_type and "text" not in content_type:
            continue
        html = fetched.get("content", "") or ""
        score, title, excerpt = _score_page(html, keywords)
        pages.append(
            {
                "url": fetched.get("url_final") or url,
                "title": title,
                "excerpt": excerpt,
                "score": score,
                "depth": depth,
            }
        )
        if depth < max_depth:
            for link in _extract_links(url, html):
                if link in visited:
                    continue
                if same_origin:
                    p = urllib.parse.urlparse(link)
                    if (
                        p.scheme != seed.scheme
                        or (p.hostname or "").lower() != seed.hostname.lower()
                    ):
                        continue
                queue.append((link, depth + 1))

    # Sort by score desc, then depth asc for tiebreak.
    pages.sort(key=lambda p: (-p["score"], p["depth"]))
    return json.dumps(
        {"pages": pages, "count": len(pages), "origin": seed_origin},
        ensure_ascii=False,
    )


registry.register("web_crawl", "web", _SCHEMA_CRAWL, _handle_web_crawl)


# ---------------------------------------------------------------------
# web_read_sitemap
# ---------------------------------------------------------------------
_SCHEMA_SITEMAP: dict[str, Any] = {
    "name": "web_read_sitemap",
    "description": (
        "Fetch & parse an XML sitemap. Falls back to sitemap_index.xml "
        "if the primary sitemap 404s. Recursively unfurls sitemap "
        "indexes. Returns list of {url, lastmod}."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "sitemap_url": {
                "type": "string",
                "description": (
                    "Absolute sitemap URL, or a bare domain "
                    "(e.g. 'docs.python.org') in which case /sitemap.xml "
                    "is assumed."
                ),
            },
        },
        "required": ["sitemap_url"],
    },
}


_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def _normalize_sitemap_input(inp: str) -> list[str]:
    """Return candidate URLs to try, in priority order."""
    inp = inp.strip()
    if not inp:
        return []
    if inp.startswith(("http://", "https://")):
        # If the user passed a URL, also build a /sitemap_index.xml
        # sibling as a fallback.
        parsed = urllib.parse.urlparse(inp)
        alt = urllib.parse.urlunparse(parsed._replace(path="/sitemap_index.xml"))
        return [inp, alt] if inp != alt else [inp]
    # Bare domain: default to /sitemap.xml, with /sitemap_index.xml fallback.
    host = inp.strip("/")
    return [
        f"https://{host}/sitemap.xml",
        f"https://{host}/sitemap_index.xml",
    ]


def _parse_sitemap_xml(xml_text: str) -> tuple[list[dict[str, str]], list[str]]:
    """Return ``(urls, child_sitemaps)``.

    Sitemaps come in two flavours: a ``<urlset>`` (leaf pages) or a
    ``<sitemapindex>`` (links to more sitemaps). We fall through to both
    element names so sites that omit the namespace still parse.
    """
    urls: list[dict[str, str]] = []
    children: list[str] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return urls, children
    # Walk URL entries (with or without namespace).
    # NB: ElementTree's ``Element.__bool__`` evaluates to False when the
    # element has no children, which is the common case for <loc>/<lastmod>
    # leaf nodes. We therefore avoid ``elem_ns or elem_nonns`` shortcuts
    # and fall through via explicit ``is None`` checks below.
    url_elems = root.findall(".//sm:url", _SITEMAP_NS)
    if not url_elems:
        url_elems = root.findall(".//url")
    for url_elem in url_elems:
        loc = url_elem.find("sm:loc", _SITEMAP_NS)
        if loc is None:
            loc = url_elem.find("loc")
        lastmod = url_elem.find("sm:lastmod", _SITEMAP_NS)
        if lastmod is None:
            lastmod = url_elem.find("lastmod")
        if loc is None or not (loc.text or "").strip():
            continue
        urls.append(
            {
                "url": loc.text.strip(),
                "lastmod": (lastmod.text or "").strip() if lastmod is not None else "",
            }
        )
    # Walk child sitemap indexes.
    sm_elems = root.findall(".//sm:sitemap", _SITEMAP_NS)
    if not sm_elems:
        sm_elems = root.findall(".//sitemap")
    for sm_elem in sm_elems:
        loc = sm_elem.find("sm:loc", _SITEMAP_NS)
        if loc is None:
            loc = sm_elem.find("loc")
        if loc is not None and (loc.text or "").strip():
            children.append(loc.text.strip())
    return urls, children


def _handle_web_read_sitemap(args: dict[str, Any], task_id: str) -> str:
    inp = str(args.get("sitemap_url", "") or "").strip()
    if not inp:
        return _err("sitemap_url is required", retriable=False)

    candidates = _normalize_sitemap_input(inp)
    visited: set[str] = set()
    collected: list[dict[str, str]] = []
    queue: deque[str] = deque(candidates)

    tried_any = False
    success = False
    max_children = 10  # guardrail: don't recurse forever on adversarial sitemaps
    while queue and len(collected) < 50_000:
        url = queue.popleft()
        if url in visited:
            continue
        visited.add(url)
        fetched = _fetch_one(url, timeout=10.0)
        tried_any = True
        if "error" in fetched:
            continue
        status = fetched.get("status", 0)
        if status == 404:
            continue
        content = fetched.get("content", "") or ""
        if not content.strip():
            continue
        urls, children = _parse_sitemap_xml(content)
        if urls:
            collected.extend(urls)
            success = True
        for child in children[:max_children]:
            if child not in visited:
                queue.append(child)
        if children:
            success = True

    if not tried_any:
        return _err("no sitemap candidates resolved", retriable=False)
    if not success:
        return _err("sitemap empty or unreadable", retriable=True)
    # Dedup collected URLs while preserving order.
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for entry in collected:
        if entry["url"] in seen:
            continue
        seen.add(entry["url"])
        deduped.append(entry)
    return json.dumps(
        {"urls": deduped, "count": len(deduped)},
        ensure_ascii=False,
    )


registry.register(
    "web_read_sitemap", "web", _SCHEMA_SITEMAP, _handle_web_read_sitemap
)
