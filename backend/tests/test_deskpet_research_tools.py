"""Tests for deskpet.tools.research_tools — DeepResearch orchestrator.

Coverage:
  * parse_sub_questions: JSON / fenced JSON / bullet fallback / dedup
  * authority_for_url: known domain, subdomain parent fallback, unknown
  * score_passage: length, coverage, authority interact correctly
  * find_footnote_refs / cite_check
  * _parse_ddg_results: real DDG-like HTML → URL+title+snippet
  * _clean_ddg_url: unwraps /l/?uddg=ENCODED
  * default_search: monkey-patched httpx returns canned HTML
  * default_extract: trafilatura path + title fallback + error
  * research_run end-to-end with mocked LLM + search + extract:
      - happy path: full pipeline, citations, cite_check ok
      - empty topic
      - LLM plan fails → fallback to topic itself
      - all searches fail → no_results report
      - all extracts fail → no_results report
      - LLM synth fails → passages-only fallback
      - cite_check missing footnotes → warning appended, errors set
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import pytest

from deskpet.tools import research_tools as r
from deskpet.tools.research_tools import (
    Citation,
    Passage,
    ResearchReport,
    authority_for_url,
    cite_check,
    find_footnote_refs,
    parse_sub_questions,
    research_run,
    score_passage,
    _clean_ddg_url,
    _parse_ddg_results,
    _parse_ddg_results_regex,
    _topic_keywords,
)


# ----------------------------------------------------------------------
# Sub-question planner parsing
# ----------------------------------------------------------------------


def test_parse_sub_questions_plain_json() -> None:
    out = parse_sub_questions('["a?", "b?", "c?"]', max_questions=5)
    assert out == ["a?", "b?", "c?"]


def test_parse_sub_questions_fenced() -> None:
    raw = '```json\n["a?", "b?"]\n```'
    out = parse_sub_questions(raw, max_questions=5)
    assert out == ["a?", "b?"]


def test_parse_sub_questions_prose_around_json() -> None:
    raw = 'Sure:\n["x?", "y?"]\nOk.'
    out = parse_sub_questions(raw, max_questions=5)
    assert out == ["x?", "y?"]


def test_parse_sub_questions_bullet_fallback() -> None:
    raw = "1. Is this question one?\n2. And here is question two?"
    out = parse_sub_questions(raw, max_questions=5)
    assert len(out) == 2
    assert "?" in out[0] and "?" in out[1]


def test_parse_sub_questions_dedup_and_cap() -> None:
    raw = '["a?", "a?", "b?", "c?", "d?", "e?"]'
    out = parse_sub_questions(raw, max_questions=3)
    assert out == ["a?", "b?", "c?"]


def test_parse_sub_questions_empty() -> None:
    assert parse_sub_questions("", max_questions=5) == []
    assert parse_sub_questions("garbage no question", max_questions=5) == []


# ----------------------------------------------------------------------
# Authority + score
# ----------------------------------------------------------------------


def test_authority_known() -> None:
    assert authority_for_url("https://en.wikipedia.org/wiki/X") >= 1.4
    assert authority_for_url("https://arxiv.org/abs/2401.0001") >= 1.4
    assert authority_for_url("https://quora.com/q") < 1.0


def test_authority_subdomain_falls_back_to_parent() -> None:
    # 'docs.python.org' is listed; the bare 'python.org' is also listed.
    assert authority_for_url("https://docs.python.org/3/") >= 1.4
    # Unknown subdomain of a known parent: 'foo.python.org' should still bonus
    assert authority_for_url("https://foo.python.org/") >= 1.0


def test_authority_unknown_default() -> None:
    assert authority_for_url("https://random.example.com/x") == 1.0
    assert authority_for_url("not a url") == 1.0
    assert authority_for_url("") == 1.0


def test_score_passage_empty_text_zero() -> None:
    assert score_passage("", keywords=["x"], url="https://x") == 0.0


def test_score_passage_length_cap() -> None:
    short = score_passage("x" * 100, keywords=[], url="https://e.com")
    long = score_passage("x" * 5000, keywords=[], url="https://e.com")
    assert long >= short
    # length component capped at 0.6 (length_norm = 1.0 × 0.6)
    assert long <= 0.6 + 0.4 + 0.001


def test_score_passage_coverage_helps() -> None:
    text = "Quantum computing uses qubits and superposition."
    scored_with = score_passage(text, keywords=["qubits", "superposition"], url="https://e.com")
    scored_without = score_passage(text, keywords=["zebra", "umbrella"], url="https://e.com")
    assert scored_with > scored_without


def test_score_passage_authority_bonus() -> None:
    text = "x" * 2000
    high = score_passage(text, keywords=[], url="https://wikipedia.org/wiki/X")
    low = score_passage(text, keywords=[], url="https://example.com/x")
    assert high > low


# ----------------------------------------------------------------------
# Footnote / cite check
# ----------------------------------------------------------------------


def test_find_footnote_refs() -> None:
    text = "Foo [^1] bar [^3] baz [^1]."
    assert find_footnote_refs(text) == [1, 3, 1]
    assert find_footnote_refs("") == []
    assert find_footnote_refs("no refs") == []


def test_cite_check_all_ok() -> None:
    cits = [
        Citation(n=1, url="https://a", title="A", snippet="", fetched_at=0.0),
        Citation(n=2, url="https://b", title="B", snippet="", fetched_at=0.0),
    ]
    res = cite_check("Claim one [^1]. Claim two [^2].", cits)
    assert res["ok"] is True
    assert res["missing"] == []
    assert res["unused"] == []
    assert res["total_refs"] == 2


def test_cite_check_missing() -> None:
    cits = [Citation(n=1, url="https://a", title="A", snippet="", fetched_at=0.0)]
    res = cite_check("Claim [^1] and [^5].", cits)
    assert res["ok"] is False
    assert res["missing"] == [5]
    assert res["unused"] == []


def test_cite_check_unused() -> None:
    cits = [
        Citation(n=1, url="https://a", title="A", snippet="", fetched_at=0.0),
        Citation(n=2, url="https://b", title="B", snippet="", fetched_at=0.0),
    ]
    res = cite_check("Only one ref [^1].", cits)
    assert res["ok"] is True
    assert res["unused"] == [2]


# ----------------------------------------------------------------------
# DDG URL cleaner + parser
# ----------------------------------------------------------------------


def test_clean_ddg_url_unwraps_redirect() -> None:
    wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%2Fb"
    assert _clean_ddg_url(wrapped) == "https://example.com/a/b"


def test_clean_ddg_url_passes_through_real() -> None:
    assert _clean_ddg_url("https://en.wikipedia.org/wiki/X") == "https://en.wikipedia.org/wiki/X"


def test_clean_ddg_url_empty() -> None:
    assert _clean_ddg_url("") == ""


_DDG_SAMPLE_HTML = """
<html><body>
  <div class="result results_links_deep">
    <h2><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fa.example.com%2Fpage1">
        Page One Title
    </a></h2>
    <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fa.example.com%2Fpage1">
        Snippet one with details.
    </a>
  </div>
  <div class="result">
    <h2><a class="result__a" href="https://wikipedia.org/wiki/Foo">Wikipedia Foo</a></h2>
    <a class="result__snippet">Foo is a metasyntactic.</a>
  </div>
</body></html>
"""


def test_parse_ddg_results_selectolax() -> None:
    pytest.importorskip("selectolax")
    out = _parse_ddg_results(_DDG_SAMPLE_HTML, max_results=10)
    assert len(out) == 2
    assert out[0]["url"] == "https://a.example.com/page1"
    assert out[0]["title"] == "Page One Title"
    assert "Snippet one" in out[0]["snippet"]
    assert out[1]["url"] == "https://wikipedia.org/wiki/Foo"


def test_parse_ddg_results_regex_fallback() -> None:
    out = _parse_ddg_results_regex(_DDG_SAMPLE_HTML, max_results=10)
    assert len(out) >= 1
    # First entry's URL should be unwrapped
    assert any("a.example.com" in r["url"] for r in out)


def test_parse_ddg_results_respects_max() -> None:
    out = _parse_ddg_results(_DDG_SAMPLE_HTML, max_results=1)
    assert len(out) == 1


def test_topic_keywords_basic() -> None:
    kws = _topic_keywords("Quantum computing in 2026 — qubits, error correction")
    # 2026 is short but a "year" so unlikely to be kept — that's OK.
    # We mostly care that meaningful tokens land in the list.
    assert "quantum" in kws
    assert "computing" in kws
    assert "qubits" in kws


def test_topic_keywords_cjk() -> None:
    kws = _topic_keywords("人工智能 大语言模型 应用场景")
    assert any(_contains_chinese(k) for k in kws)


def _contains_chinese(s: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in s)


# ----------------------------------------------------------------------
# default_search + default_extract — light integration with mocked httpx
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_search_with_mock_client() -> None:
    import httpx

    captured: dict[str, Any] = {}

    class _MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            captured["url"] = str(request.url)
            captured["method"] = request.method
            return httpx.Response(200, text=_DDG_SAMPLE_HTML)

    client = httpx.AsyncClient(transport=_MockTransport())
    try:
        out = await r.default_search("test query", max_results=5, client=client)
    finally:
        await client.aclose()
    assert len(out) == 2
    assert "html.duckduckgo.com" in captured["url"]
    assert captured["method"] == "POST"


@pytest.mark.asyncio
async def test_default_search_network_failure_returns_empty() -> None:
    import httpx

    class _BoomTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("simulated")

    client = httpx.AsyncClient(transport=_BoomTransport())
    try:
        out = await r.default_search("q", client=client)
    finally:
        await client.aclose()
    assert out == []


@pytest.mark.asyncio
async def test_default_extract_happy_path() -> None:
    import httpx

    sample_html = (
        "<html><head><title>An Article</title></head>"
        "<body><article>"
        + ("This is the article body. " * 80) +
        "</article></body></html>"
    )

    class _T(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(200, text=sample_html)

    client = httpx.AsyncClient(transport=_T())
    try:
        out = await r.default_extract("https://e.com/x", client=client)
    finally:
        await client.aclose()
    assert out["ok"] is True
    assert out["url"] == "https://e.com/x"
    assert "article body" in out["text"]
    # Title may come from trafilatura metadata or our <title> fallback
    assert "Article" in out["title"]


@pytest.mark.asyncio
async def test_default_extract_404_returns_error() -> None:
    import httpx

    class _T(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(404, text="not found")

    client = httpx.AsyncClient(transport=_T())
    try:
        out = await r.default_extract("https://e.com/x", client=client)
    finally:
        await client.aclose()
    assert out["ok"] is False
    assert "error" in out


# ----------------------------------------------------------------------
# research_run — end-to-end with full mocks
# ----------------------------------------------------------------------


class FakeLLM:
    def __init__(self, responses: list[str | Exception]) -> None:
        self._r = list(responses)
        self.calls: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        item = self._r.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_search(canned: dict[str, list[dict[str, Any]]]):
    async def _search(query: str, *, max_results: int = 5):
        return canned.get(query, [])[:max_results]
    return _search


def make_extract(canned: dict[str, dict[str, Any]]):
    async def _extract(url: str):
        return canned.get(url, {"ok": False, "error": "no fixture", "url": url})
    return _extract


@pytest.mark.asyncio
async def test_research_run_happy_path() -> None:
    plan_resp = json.dumps([
        "What is quantum computing?",
        "Why does it matter in 2026?",
    ])
    synth_resp = (
        "# Quantum computing in 2026\n\n"
        "## TL;DR\n\n"
        "Quantum computing today uses noisy qubits and is years from broad utility. \n\n"
        "## Background\n\n"
        "Quantum hardware has scaled past 1000 qubits but error correction "
        "remains the bottleneck [^1].\n\n"
        "## Current state\n\n"
        "Vendors are racing to lower error rates [^2].\n\n"
        "## Open questions / controversies\n\n"
        "Whether near-term advantage is real is hotly debated [^1][^2].\n\n"
        "## What's next\n\n"
        "Fault tolerance is the prize [^1].\n"
    )
    llm = FakeLLM([plan_resp, synth_resp])

    search = make_search({
        "What is quantum computing?": [
            {"url": "https://wikipedia.org/qc", "title": "QC", "snippet": "..."},
            {"url": "https://nature.com/articles/qc", "title": "Nature QC", "snippet": "..."},
        ],
        "Why does it matter in 2026?": [
            {"url": "https://arxiv.org/abs/2401.0001", "title": "Arxiv", "snippet": "..."},
        ],
    })
    extract = make_extract({
        "https://wikipedia.org/qc": {
            "ok": True, "url": "https://wikipedia.org/qc",
            "title": "Quantum computing — Wikipedia",
            "text": "Quantum computing is a model that uses qubits "
                    "and superposition. " * 50,
            "fetched_at": time.time(),
        },
        "https://nature.com/articles/qc": {
            "ok": True, "url": "https://nature.com/articles/qc",
            "title": "Quantum hardware scaling — Nature",
            "text": "Modern quantum hardware has scaled past 1000 physical "
                    "qubits with error rates near 0.1%. " * 30,
            "fetched_at": time.time(),
        },
        "https://arxiv.org/abs/2401.0001": {
            "ok": True, "url": "https://arxiv.org/abs/2401.0001",
            "title": "On the value of quantum supremacy",
            "text": "Near-term quantum advantage remains contested. " * 40,
            "fetched_at": time.time(),
        },
    })

    report = await research_run(
        "Quantum computing in 2026",
        llm_call=llm,
        search=search,
        extract=extract,
        max_sub_questions=2,
        max_urls_per_query=2,
    )

    assert isinstance(report, ResearchReport)
    assert report.topic == "Quantum computing in 2026"
    assert len(report.citations) >= 2
    assert report.coverage["n_sources"] >= 2
    assert report.coverage["n_sub_questions"] == 2
    assert report.coverage["cite_check_ok"] is True
    # Footnotes appear in the report and are valid
    assert "[^1]" in report.report_md
    # Citation appendix appended
    assert "## 引用" in report.report_md
    assert "[^1]: " in report.report_md
    # TL;DR was extracted as summary
    assert report.summary != ""
    assert "quantum" in report.summary.lower()


@pytest.mark.asyncio
async def test_research_run_empty_topic() -> None:
    llm = FakeLLM([])
    report = await research_run("", llm_call=llm)
    assert report.errors == ["empty topic"]
    assert report.citations == []
    assert report.report_md == ""


@pytest.mark.asyncio
async def test_research_run_llm_plan_fails_falls_back_to_topic() -> None:
    """When the planner LLM raises, we treat the topic itself as the
    single sub-question and keep going."""
    llm = FakeLLM([
        RuntimeError("plan down"),
        # synth still works
        "# T\n\n## TL;DR\n\nSummary.\n\n## Background\n\nFact [^1].",
    ])
    search = make_search({
        "deep topic": [{"url": "https://x.com", "title": "X", "snippet": ""}],
    })
    extract = make_extract({
        "https://x.com": {
            "ok": True, "url": "https://x.com", "title": "X",
            "text": "Long sample text about deep topic. " * 40,
            "fetched_at": time.time(),
        },
    })
    report = await research_run(
        "deep topic", llm_call=llm, search=search, extract=extract,
    )
    assert report.sub_questions == ["deep topic"]
    assert any("plan_fallback" in e for e in report.errors)
    assert len(report.citations) == 1


@pytest.mark.asyncio
async def test_research_run_all_searches_fail() -> None:
    llm = FakeLLM([json.dumps(["q1?", "q2?"])])
    search = make_search({})  # nothing maps → empty results
    extract = make_extract({})
    report = await research_run(
        "topic", llm_call=llm, search=search, extract=extract,
    )
    assert report.citations == []
    assert any("no search results" in e for e in report.errors)
    assert "未能找到可用的来源" in report.report_md or "未能找到" in report.report_md


@pytest.mark.asyncio
async def test_research_run_all_extracts_fail() -> None:
    llm = FakeLLM([json.dumps(["q?"])])
    search = make_search({
        "q?": [{"url": "https://a.com", "title": "A", "snippet": ""}],
    })
    extract = make_extract({})  # every URL → fixture miss → ok=False
    report = await research_run(
        "topic", llm_call=llm, search=search, extract=extract,
    )
    assert report.citations == []
    assert any("no usable passages" in e for e in report.errors)


@pytest.mark.asyncio
async def test_research_run_synth_llm_failure_uses_passages_fallback() -> None:
    plan = json.dumps(["q?"])
    llm = FakeLLM([plan, RuntimeError("synth down")])
    search = make_search({
        "q?": [{"url": "https://wikipedia.org/x", "title": "W", "snippet": ""}],
    })
    extract = make_extract({
        "https://wikipedia.org/x": {
            "ok": True, "url": "https://wikipedia.org/x", "title": "W",
            "text": "Important fact about the topic. " * 60,
            "fetched_at": time.time(),
        },
    })
    report = await research_run(
        "topic", llm_call=llm, search=search, extract=extract,
    )
    assert len(report.citations) == 1
    # Passages-only fallback always includes [^N] references for every
    # cited passage, so cite_check still passes.
    assert "[^1]" in report.report_md
    assert report.coverage["cite_check_ok"] is True
    assert any("synth_llm" in e for e in report.errors)


@pytest.mark.asyncio
async def test_research_run_cite_check_catches_missing_footnotes() -> None:
    plan = json.dumps(["q?"])
    bad_synth = (
        "# T\n\n## TL;DR\n\nClaim [^1] [^99].\n\n## Background\n\nMore [^5]."
    )
    llm = FakeLLM([plan, bad_synth])
    search = make_search({
        "q?": [{"url": "https://wikipedia.org/x", "title": "W", "snippet": ""}],
    })
    extract = make_extract({
        "https://wikipedia.org/x": {
            "ok": True, "url": "https://wikipedia.org/x", "title": "W",
            "text": "Verified factoid about the topic. " * 60,
            "fetched_at": time.time(),
        },
    })
    report = await research_run(
        "topic", llm_call=llm, search=search, extract=extract,
    )
    assert report.coverage["cite_check_ok"] is False
    assert report.coverage["cite_missing"] == [5, 99]
    # Warning appended to the report so caller sees it
    assert "⚠️" in report.report_md or "自检发现" in report.report_md
    assert any("cite_check failed" in e for e in report.errors)


@pytest.mark.asyncio
async def test_research_run_skips_short_passages() -> None:
    plan = json.dumps(["q?"])
    synth = "# T\n## TL;DR\n\nx\n## Background\n\n[^1]"
    llm = FakeLLM([plan, synth])
    search = make_search({
        "q?": [
            {"url": "https://a.com", "title": "A", "snippet": ""},
            {"url": "https://b.com", "title": "B", "snippet": ""},
        ],
    })
    # 'a.com' returns just 50 chars — must be filtered out;
    # 'b.com' returns a long passage and survives.
    extract = make_extract({
        "https://a.com": {
            "ok": True, "url": "https://a.com", "title": "A",
            "text": "too short", "fetched_at": time.time(),
        },
        "https://b.com": {
            "ok": True, "url": "https://b.com", "title": "B",
            "text": "x" * 500, "fetched_at": time.time(),
        },
    })
    report = await research_run(
        "topic", llm_call=llm, search=search, extract=extract,
        min_passage_chars=300,
    )
    # Only b.com made it through
    assert len(report.citations) == 1
    assert report.citations[0].url == "https://b.com"


@pytest.mark.asyncio
async def test_research_run_authority_ordered_first() -> None:
    """High-authority source should outrank a longer low-authority one."""
    plan = json.dumps(["q?"])
    synth = (
        "# T\n## TL;DR\n\nx\n## Background\n\nClaim [^1] and another [^2]."
    )
    llm = FakeLLM([plan, synth])
    search = make_search({
        "q?": [
            {"url": "https://obscure.com/long", "title": "Obscure", "snippet": ""},
            {"url": "https://wikipedia.org/auth", "title": "Wiki", "snippet": ""},
        ],
    })
    extract = make_extract({
        "https://obscure.com/long": {
            "ok": True, "url": "https://obscure.com/long", "title": "Obscure",
            "text": "obscure but long content about the topic. " * 60,
            "fetched_at": time.time(),
        },
        "https://wikipedia.org/auth": {
            "ok": True, "url": "https://wikipedia.org/auth", "title": "Wikipedia",
            "text": "wikipedia content covering the topic well. " * 30,
            "fetched_at": time.time(),
        },
    })
    report = await research_run(
        "topic", llm_call=llm, search=search, extract=extract,
    )
    # Wikipedia should land at #1 due to authority bonus
    assert report.citations[0].url == "https://wikipedia.org/auth"


@pytest.mark.asyncio
async def test_research_run_report_appends_citation_list() -> None:
    plan = json.dumps(["q?"])
    synth = "# T\n\n## TL;DR\n\nClaim [^1]."
    llm = FakeLLM([plan, synth])
    search = make_search({
        "q?": [{"url": "https://wikipedia.org/x", "title": "Wiki", "snippet": ""}],
    })
    extract = make_extract({
        "https://wikipedia.org/x": {
            "ok": True, "url": "https://wikipedia.org/x", "title": "Wiki",
            "text": "content. " * 80, "fetched_at": time.time(),
        },
    })
    report = await research_run(
        "topic", llm_call=llm, search=search, extract=extract,
    )
    # Footnote definition appears in the appendix
    assert "[^1]: [Wiki](https://wikipedia.org/x)" in report.report_md


@pytest.mark.asyncio
async def test_research_report_as_dict_is_json_serializable() -> None:
    plan = json.dumps(["q?"])
    synth = "# T\n\n## TL;DR\n\nx [^1]"
    llm = FakeLLM([plan, synth])
    search = make_search({
        "q?": [{"url": "https://a.com", "title": "A", "snippet": ""}],
    })
    extract = make_extract({
        "https://a.com": {
            "ok": True, "url": "https://a.com", "title": "A",
            "text": "sample content. " * 40, "fetched_at": time.time(),
        },
    })
    report = await research_run("topic", llm_call=llm, search=search, extract=extract)
    d = report.as_dict()
    # Roundtrip via json.dumps must succeed
    blob = json.dumps(d, ensure_ascii=False)
    assert "topic" in blob
    assert "citations" in blob


@pytest.mark.asyncio
async def test_research_run_dedups_urls_across_sub_questions() -> None:
    """If the same URL appears in two sub-queries' results, we should
    fetch it only once."""
    plan = json.dumps(["q1?", "q2?"])
    synth = "# T\n## TL;DR\n\nClaim [^1]."
    llm = FakeLLM([plan, synth])
    common = {"url": "https://shared.com/x", "title": "Shared", "snippet": ""}
    search = make_search({"q1?": [common], "q2?": [common]})

    extract_calls: list[str] = []

    async def _extract(url: str):
        extract_calls.append(url)
        return {
            "ok": True, "url": url, "title": "Shared",
            "text": "shared content. " * 50, "fetched_at": time.time(),
        }

    report = await research_run(
        "topic", llm_call=llm, search=search, extract=_extract,
    )
    assert len(extract_calls) == 1
    assert len(report.citations) == 1
