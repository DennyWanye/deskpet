# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S22 — web_search tool (DDG HTML scrape, mocked httpx)."""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from deskpet.tools.code_tools.web_search_tool import web_search


# A pared-down DDG HTML response — just enough structure to exercise
# the regex parser. Real responses are ~70 KB; we keep this small.
_FAKE_DDG_HTML = """
<html><body>
<div class="result">
  <h2><a class="result__a" href="https://example.com/python">Python tutorial</a></h2>
  <a class="result__snippet">Learn Python from scratch with our friendly guide.</a>
</div>
<div class="result">
  <h2><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A//docs.python.org/3/&rut=abc">Python 3 docs</a></h2>
  <a class="result__snippet">The official Python 3 documentation hub.</a>
</div>
<div class="result">
  <h2><a class="result__a" href="https://example.com/three">Third result</a></h2>
  <a class="result__snippet">A third match for the query.</a>
</div>
</body></html>
"""


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError(
                "fail", request=None, response=None  # type: ignore[arg-type]
            )


def test_web_search_parses_results():
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = False
        mock_client.post.return_value = _FakeResponse(_FAKE_DDG_HTML)
        mock_client_cls.return_value = mock_client

        out = json.loads(web_search({"query": "python", "max_results": 5}))

    assert out["query"] == "python"
    assert out["count"] == 3
    assert out["results"][0]["title"] == "Python tutorial"
    assert out["results"][0]["url"] == "https://example.com/python"
    # uddg redirector should be unwrapped to the real URL
    assert out["results"][1]["url"] == "https://docs.python.org/3/"


def test_web_search_caps_at_max_results():
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = False
        mock_client.post.return_value = _FakeResponse(_FAKE_DDG_HTML)
        mock_client_cls.return_value = mock_client

        out = json.loads(web_search({"query": "x", "max_results": 1}))

    assert out["count"] == 1


def test_web_search_caps_at_hard_max():
    """User asks for 999 → we still cap at 10."""
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = False
        mock_client.post.return_value = _FakeResponse(_FAKE_DDG_HTML)
        mock_client_cls.return_value = mock_client

        out = json.loads(web_search({"query": "x", "max_results": 999}))

    assert out["count"] <= 10


def test_web_search_handles_network_error():
    """DDG times out / 429 / DNS error → empty results, NOT exception."""
    import httpx

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = False
        mock_client.post.side_effect = httpx.ConnectError("dns failed")
        mock_client_cls.return_value = mock_client

        out = json.loads(web_search({"query": "x"}))

    assert out["results"] == []
    assert "error" in out


def test_web_search_requires_query():
    out = json.loads(web_search({}))
    assert "error" in out


def test_web_search_handles_empty_html():
    """DDG returns 200 but empty body → 0 results, no exception."""
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = False
        mock_client.post.return_value = _FakeResponse("<html></html>")
        mock_client_cls.return_value = mock_client

        out = json.loads(web_search({"query": "x"}))

    assert out["count"] == 0
