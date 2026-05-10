"""web_search — DuckDuckGo HTML scrape.

We picked DDG because it doesn't require an API key (Bing/Google do)
and the HTML is unauthenticated + relatively stable. The endpoint we
hit is the user-facing ``/html/`` lite version which returns clean
``<a class="result__a">`` links and ``<a class="result__snippet">``
descriptions.

Failure mode: when DDG changes its HTML class names or rate-limits us,
we return ``{"results": [], "error": "..."}``. The LLM should retry or
fall back to whatever knowledge it already has — this is a best-effort
tool.

We ALWAYS truncate to ``max_results`` (default 5, hard cap 10) so the
LLM doesn't burn tokens on a wall of search noise.
"""
from __future__ import annotations

import json
import logging
import re
from html import unescape
from typing import Any

import httpx

log = logging.getLogger(__name__)

_DDG_URL = "https://html.duckduckgo.com/html/"
_RESULT_BLOCK_RE = re.compile(
    r'<a class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?<a class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_MAX_RESULTS_CAP = 10


def _strip_tags(html: str) -> str:
    text = _TAG_STRIP_RE.sub("", html)
    return unescape(text).strip()


def web_search(args: dict[str, Any], task_id: str = "") -> str:
    query = args.get("query")
    if not query or not isinstance(query, str):
        return json.dumps({"error": "query (string) is required"})

    try:
        max_results = int(args.get("max_results", 5))
    except (TypeError, ValueError):
        max_results = 5
    max_results = max(1, min(max_results, _MAX_RESULTS_CAP))

    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            resp = client.post(
                _DDG_URL,
                data={"q": query},
                headers={
                    # Some user agents get rate-limited; mimic a desktop
                    # browser to be friendly.
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0 Safari/537.36"
                    ),
                },
            )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("web_search HTTP error: %s", e)
        return json.dumps({"query": query, "results": [], "error": str(e)})

    results: list[dict[str, str]] = []
    for m in _RESULT_BLOCK_RE.finditer(resp.text):
        url, title_html, snippet_html = m.group(1), m.group(2), m.group(3)
        title = _strip_tags(title_html)
        snippet = _strip_tags(snippet_html)
        if not title or not url:
            continue
        # DDG wraps URLs in /l/?uddg=... redirector for some links —
        # extract the real target if present.
        if url.startswith("//duckduckgo.com/l/?uddg=") or "uddg=" in url:
            try:
                from urllib.parse import urlparse, parse_qs, unquote

                qs = parse_qs(urlparse("https:" + url if url.startswith("//") else url).query)
                if "uddg" in qs:
                    url = unquote(qs["uddg"][0])
            except Exception:
                pass
        results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= max_results:
            break

    return json.dumps(
        {
            "query": query,
            "count": len(results),
            "results": results,
        },
        ensure_ascii=False,
    )
