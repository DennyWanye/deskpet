"""P4-S5: tool_search meta-tool tests (tool-framework spec).

Covers the "Tool Search for Lazy Schema Loading" requirement:

* query matches against name + description (case-insensitive).
* every token must appear (all-terms semantics).
* toolset filter narrows results.
* tool_search itself is excluded from matches to avoid recursive
  surfacing.
* matches come back in OpenAI function-calling envelope shape so the
  agent can forward them straight to a follow-up turn.
"""
from __future__ import annotations

import json

import pytest

from deskpet.tools.registry import registry


def test_tool_search_finds_file_tools():
    out = json.loads(registry.dispatch("tool_search", {"query": "file"}))
    names = [m["function"]["name"] for m in out["matches"]]
    assert out["count"] >= 4
    # All four file_* should turn up.
    for expected in ("file_read", "file_write", "file_glob", "file_grep"):
        assert expected in names


def test_tool_search_returns_openai_envelope():
    out = json.loads(registry.dispatch("tool_search", {"query": "write"}))
    assert isinstance(out["matches"], list)
    for entry in out["matches"]:
        assert entry["type"] == "function"
        assert "name" in entry["function"]
        assert "parameters" in entry["function"]


def test_tool_search_excludes_itself():
    out = json.loads(registry.dispatch("tool_search", {"query": "tool"}))
    names = [m["function"]["name"] for m in out["matches"]]
    assert "tool_search" not in names


def test_tool_search_all_tokens_required():
    # "crawl xyz123_nonexistent" should produce 0 matches because no
    # tool description contains the second token.
    out = json.loads(
        registry.dispatch("tool_search", {"query": "crawl xyz123_nonexistent"})
    )
    assert out["count"] == 0


def test_tool_search_toolset_filter():
    out = json.loads(
        registry.dispatch(
            "tool_search", {"query": "read", "toolset": "memory"}
        )
    )
    names = [m["function"]["name"] for m in out["matches"]]
    # "read" appears in memory_read's description.
    assert "memory_read" in names
    # file_read (toolset=file) MUST be filtered out.
    assert "file_read" not in names


def test_tool_search_rejects_empty_query():
    out = json.loads(registry.dispatch("tool_search", {"query": "   "}))
    assert out["error"].startswith("query")
    assert out["retriable"] is False


def test_tool_search_case_insensitive():
    lower = json.loads(registry.dispatch("tool_search", {"query": "write"}))
    upper = json.loads(registry.dispatch("tool_search", {"query": "WRITE"}))
    lower_names = sorted(m["function"]["name"] for m in lower["matches"])
    upper_names = sorted(m["function"]["name"] for m in upper["matches"])
    assert lower_names == upper_names
