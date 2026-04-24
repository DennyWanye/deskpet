"""P4-S5: web tool tests.

We mock the network layer (httpx) with ``respx`` if available, else
with a hand-rolled ``MockTransport`` — the latter is always available
with ``httpx >= 0.25`` so the suite never falls back to real internet.

Shared state (rate limit, robots cache, block cache) is reset between
tests via a ``reset_web_state`` autouse fixture; otherwise one test's
throttling would slow the next, and the block cache would stick around
for 1h on a 429 test.

A ``@pytest.mark.perf`` local-server micro-benchmark asserts web_fetch
p95 < 2s. We loop a small number of iterations (20) because CI can be
noisy; threshold is generous.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest

from deskpet.tools import web_tools
from deskpet.tools._config import reset_cache as _reset_config_cache
from deskpet.tools.registry import registry


# ---------------------------------------------------------------------
# Autouse: reset module state between tests
# ---------------------------------------------------------------------
@pytest.fixture(autouse=True)
def reset_web_state():
    web_tools._last_fetch_ts.clear()
    web_tools._robots_cache.clear()
    web_tools._block_cache.clear()
    _reset_config_cache()
    yield
    web_tools._last_fetch_ts.clear()
    web_tools._robots_cache.clear()
    web_tools._block_cache.clear()


# ---------------------------------------------------------------------
# httpx MockTransport helper
# ---------------------------------------------------------------------
def _patch_httpx_client(monkeypatch: pytest.MonkeyPatch, handler):
    """Route every httpx.Client(...) through a MockTransport that calls
    ``handler(request) -> httpx.Response``.

    The real httpx.Client keyword args (timeout, follow_redirects,
    max_redirects, headers) are preserved; we just swap in our transport.
    """
    RealClient = httpx.Client

    def _fake_client(**kwargs):
        kwargs.pop("transport", None)
        return RealClient(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(web_tools.httpx, "Client", _fake_client)


# ---------------------------------------------------------------------
# web_fetch
# ---------------------------------------------------------------------
def test_web_fetch_returns_status_content_type(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, text="")
        return httpx.Response(
            200,
            text="<html><title>T</title><body>hi</body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )

    _patch_httpx_client(monkeypatch, handler)
    out = json.loads(
        registry.dispatch("web_fetch", {"url": "https://example.com/"})
    )
    assert out["status"] == 200
    assert "text/html" in out["content_type"]
    assert "hi" in out["content"]


def test_web_fetch_user_agent_header_present(monkeypatch: pytest.MonkeyPatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        seen["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, text="ok", headers={"content-type": "text/plain"})

    _patch_httpx_client(monkeypatch, handler)
    registry.dispatch("web_fetch", {"url": "https://example.com/foo"})
    assert "DeskPet" in seen["ua"]


def test_web_fetch_respects_robots_disallow(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                text="User-agent: *\nDisallow: /private/\n",
                headers={"content-type": "text/plain"},
            )
        return httpx.Response(200, text="leaked", headers={"content-type": "text/html"})

    _patch_httpx_client(monkeypatch, handler)
    out = json.loads(
        registry.dispatch("web_fetch", {"url": "https://example.com/private/x"})
    )
    assert "error" in out
    assert "robots" in out["error"]


def test_web_fetch_timeout_returns_retriable_error(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.ReadTimeout("slow")

    _patch_httpx_client(monkeypatch, handler)
    out = json.loads(
        registry.dispatch(
            "web_fetch", {"url": "https://slow.example/", "timeout": 1}
        )
    )
    assert out["error"] == "timeout"
    assert out["retriable"] is True


def test_web_fetch_429_triggers_block_cache(monkeypatch: pytest.MonkeyPatch):
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        hits["n"] += 1
        return httpx.Response(429, text="rate-limited", headers={"content-type": "text/plain"})

    _patch_httpx_client(monkeypatch, handler)
    # Three 429s — third one should push block_cache over threshold.
    for _ in range(3):
        registry.dispatch("web_fetch", {"url": "https://rate.example/x"})
    # Fourth call should be short-circuited with 'domain temporarily blocked'.
    out = json.loads(
        registry.dispatch("web_fetch", {"url": "https://rate.example/x"})
    )
    assert out["error"] == "domain temporarily blocked"
    assert out["retriable"] is True


def test_web_fetch_enforces_rate_limit_between_calls(monkeypatch: pytest.MonkeyPatch):
    """Two sequential fetches to the same host MUST be at least
    request_interval_ms apart. We shrink the interval for the test but
    still expect the throttle to trigger."""
    # Override config to 100 ms for a fast test.
    monkeypatch.setattr(
        web_tools, "load_web_config",
        lambda: web_tools.WebToolsConfig(
            user_agent="DeskPet/test", respect_robots_txt=False,
            request_interval_ms=100, per_domain_max_concurrency=2,
            crawl_default_max_pages=5, crawl_default_max_depth=1,
            preferred_sources=[],
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok", headers={"content-type": "text/html"})

    _patch_httpx_client(monkeypatch, handler)
    t0 = time.monotonic()
    registry.dispatch("web_fetch", {"url": "https://rate.test/a"})
    registry.dispatch("web_fetch", {"url": "https://rate.test/b"})
    elapsed = time.monotonic() - t0
    # With a 100ms interval between the two, total should be ≥ 0.1s.
    assert elapsed >= 0.08, f"throttle not enforced, elapsed={elapsed:.3f}s"


# ---------------------------------------------------------------------
# web_extract_article
# ---------------------------------------------------------------------
_FIXTURE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <title>How to Write a Python Decorator</title>
  <meta name="author" content="Ada Lovelace" />
  <meta property="article:published_time" content="2024-06-15T10:30:00Z" />
</head>
<body>
  <header><nav>home | about</nav></header>
  <main>
    <article>
      <h1>How to Write a Python Decorator</h1>
      <p>Decorators are a powerful feature in Python that let you wrap
      one function inside another.</p>
      <p>The basic pattern is simple: a decorator takes a callable and
      returns a new callable. Let's look at a concrete example that
      logs every call to a wrapped function.</p>
      <p>Decorators compose: stack them and they run outside-in.
      Remember to preserve metadata with functools.wraps.</p>
    </article>
  </main>
  <footer>© 2024</footer>
</body>
</html>
"""


def test_web_extract_article_from_raw_html_gets_title_and_text():
    out = json.loads(
        registry.dispatch(
            "web_extract_article", {"url_or_html": _FIXTURE_HTML}
        )
    )
    assert out["title"] is not None
    assert "Decorator" in out["title"]
    assert out["text"] is not None and len(out["text"]) > 50
    assert "Decorators" in out["text"]


def test_web_extract_article_missing_fields_return_null():
    # Minimal HTML with just a title — author/date SHOULD be None, not raise.
    html = "<html><head><title>Bare</title></head><body><p>lonely.</p></body></html>"
    out = json.loads(
        registry.dispatch("web_extract_article", {"url_or_html": html})
    )
    # trafilatura may not extract text from a minimal body; either way it
    # MUST return a dict with the 5 keys and no crash.
    for k in ("title", "author", "date", "text", "language"):
        assert k in out


def test_web_extract_article_from_url(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=_FIXTURE_HTML, headers={"content-type": "text/html"})

    _patch_httpx_client(monkeypatch, handler)
    out = json.loads(
        registry.dispatch(
            "web_extract_article",
            {"url_or_html": "https://blog.example/decorators"},
        )
    )
    assert out["title"] and "Decorator" in out["title"]


# ---------------------------------------------------------------------
# web_read_sitemap
# ---------------------------------------------------------------------
_SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://site.example/a</loc><lastmod>2024-01-02</lastmod></url>
  <url><loc>https://site.example/b</loc><lastmod>2024-03-04</lastmod></url>
</urlset>
"""

_SITEMAP_INDEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://site.example/sitemap-a.xml</loc></sitemap>
</sitemapindex>
"""


def test_web_read_sitemap_parses_urls(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(404)
        if path == "/sitemap.xml":
            return httpx.Response(200, text=_SITEMAP_XML, headers={"content-type": "application/xml"})
        return httpx.Response(404)

    _patch_httpx_client(monkeypatch, handler)
    out = json.loads(
        registry.dispatch(
            "web_read_sitemap", {"sitemap_url": "https://site.example/sitemap.xml"}
        )
    )
    assert out["count"] == 2
    assert out["urls"][0]["url"] == "https://site.example/a"
    assert out["urls"][0]["lastmod"] == "2024-01-02"


def test_web_read_sitemap_fallback_to_index(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(404)
        if path == "/sitemap.xml":
            return httpx.Response(404)
        if path == "/sitemap_index.xml":
            return httpx.Response(200, text=_SITEMAP_INDEX_XML, headers={"content-type": "application/xml"})
        if path == "/sitemap-a.xml":
            return httpx.Response(200, text=_SITEMAP_XML, headers={"content-type": "application/xml"})
        return httpx.Response(404)

    _patch_httpx_client(monkeypatch, handler)
    out = json.loads(
        registry.dispatch(
            "web_read_sitemap", {"sitemap_url": "site.example"}
        )
    )
    assert out["count"] == 2


# ---------------------------------------------------------------------
# web_crawl
# ---------------------------------------------------------------------
def test_web_crawl_stays_same_origin(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        web_tools, "load_web_config",
        lambda: web_tools.WebToolsConfig(
            user_agent="DeskPet/test", respect_robots_txt=False,
            request_interval_ms=0, per_domain_max_concurrency=2,
            crawl_default_max_pages=5, crawl_default_max_depth=1,
            preferred_sources=[],
        ),
    )

    pages = {
        "/": """<html><body>
            <a href="/a">A</a>
            <a href="/b">B</a>
            <a href="https://other.example/x">off</a>
          </body></html>""",
        "/a": "<html><body>Alpha content</body></html>",
        "/b": "<html><body>Beta content</body></html>",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host != "site.example":
            return httpx.Response(200, text="offsite", headers={"content-type": "text/html"})
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(404)
        body = pages.get(path, "<html><body/></html>")
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    _patch_httpx_client(monkeypatch, handler)
    out = json.loads(
        registry.dispatch(
            "web_crawl",
            {
                "start_url": "https://site.example/",
                "keywords": ["alpha"],
                "max_depth": 1,
                "max_pages": 10,
            },
        )
    )
    urls = [p["url"] for p in out["pages"]]
    assert out["count"] >= 2
    # Every crawled URL MUST stay on site.example.
    for url in urls:
        assert "site.example" in url and "other.example" not in url


def test_web_crawl_keyword_scoring(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        web_tools, "load_web_config",
        lambda: web_tools.WebToolsConfig(
            user_agent="DeskPet/test", respect_robots_txt=False,
            request_interval_ms=0, per_domain_max_concurrency=2,
            crawl_default_max_pages=5, crawl_default_max_depth=1,
            preferred_sources=[],
        ),
    )

    pages = {
        "/": '<html><body><a href="/hot">h</a><a href="/cold">c</a></body></html>',
        "/hot": "<html><title>Python Python</title><body>python python python rocks</body></html>",
        "/cold": "<html><title>Rocks</title><body>nothing much</body></html>",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        body = pages.get(request.url.path, "<html/>")
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    _patch_httpx_client(monkeypatch, handler)
    out = json.loads(
        registry.dispatch(
            "web_crawl",
            {
                "start_url": "https://s.example/",
                "keywords": ["python"],
                "max_depth": 1,
                "max_pages": 10,
            },
        )
    )
    # Top page by score MUST be /hot (keyword-rich).
    assert out["pages"][0]["url"].endswith("/hot")


# ---------------------------------------------------------------------
# Perf: web_fetch p95 < 2s on a local HTTP server
# ---------------------------------------------------------------------
class _LocalHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — stdlib callback naming
        body = b"<html><title>perf</title><body>hi</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args, **kwargs):  # noqa: D401 — silence BaseHTTPRequestHandler
        return


@pytest.fixture
def local_server():
    server = HTTPServer(("127.0.0.1", 0), _LocalHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


@pytest.mark.perf
def test_web_fetch_p95_under_2s(local_server, monkeypatch: pytest.MonkeyPatch):
    """Hit a local HTTP server 20 times; p95 MUST be under 2s.

    Threshold is generous — localhost ought to be <50ms — but CI can
    have GIL contention, so we stay loose to avoid flakes.
    """
    # No throttle / robots for the test server.
    monkeypatch.setattr(
        web_tools, "load_web_config",
        lambda: web_tools.WebToolsConfig(
            user_agent="DeskPet/perf", respect_robots_txt=False,
            request_interval_ms=0, per_domain_max_concurrency=2,
            crawl_default_max_pages=5, crawl_default_max_depth=1,
            preferred_sources=[],
        ),
    )
    times: list[float] = []
    for _ in range(20):
        t0 = time.monotonic()
        out = json.loads(
            registry.dispatch("web_fetch", {"url": local_server + "/hello"})
        )
        times.append(time.monotonic() - t0)
        assert out.get("status") == 200
    times.sort()
    p95 = times[int(0.95 * (len(times) - 1))]
    assert p95 < 2.0, f"p95={p95:.3f}s exceeds 2s"
