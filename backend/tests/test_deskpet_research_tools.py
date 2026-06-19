# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

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


@pytest.fixture(autouse=True)
def _isolate_phase2(monkeypatch):
    """测试默认关掉 P2 query-expansion + direct-sources：它们会多吃一次
    llm_call / 打外网,打乱 FakeLLM([plan,synth]) 序列。需要测的用例自行
    monkeypatch 打开。"""
    monkeypatch.setattr(r, "_query_expansion_enabled", lambda: False)
    monkeypatch.setattr(r, "_direct_sources_enabled", lambda: False)
    monkeypatch.setattr(r, "_js_render_enabled", lambda: False)  # JS 渲染默认关,要测的自开
    r._RESEARCH_RAW_CACHE = None   # 清 [research] 配置缓存,防跨测试污染
    r._reset_js_render_budget()    # 归零 JS 渲染触发计数,防跨测试污染
    yield
    r._RESEARCH_RAW_CACHE = None
    r._reset_js_render_budget()


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


@pytest.mark.asyncio
async def test_research_run_reflection_round_fires_on_deep() -> None:
    """max_rounds=2: after round-1, the LLM returns a follow-up query →
    a 2nd search+extract happens and the new source joins the report."""
    plan = json.dumps(["q1?"])
    gap = json.dumps(["follow up gap query?"])  # reflection → 1 follow-up
    synth = "# T\n## TL;DR\n\nClaim [^1].\n\nMore [^2]."
    llm = FakeLLM([plan, gap, synth])  # plan → gap → synth

    search = make_search({
        "q1?": [{"url": "https://arxiv.org/r1", "title": "R1", "snippet": ""}],
        "follow up gap query?": [
            {"url": "https://nature.com/r2", "title": "R2", "snippet": ""},
        ],
    })
    extracted: list[str] = []

    async def _extract(url: str):
        extracted.append(url)
        return {
            "ok": True, "url": url, "title": url.rsplit("/", 1)[-1],
            "text": "useful research content about the topic. " * 40,
            "fetched_at": time.time(),
        }

    report = await research_run(
        "topic", llm_call=llm, search=search, extract=_extract, max_rounds=2,
    )
    # round-1 url + round-2 follow-up url both fetched
    assert "https://arxiv.org/r1" in extracted
    assert "https://nature.com/r2" in extracted
    assert report.coverage["rounds"] == 2
    assert len(report.citations) == 2


@pytest.mark.asyncio
async def test_research_run_single_round_by_default() -> None:
    """Default max_rounds=1: no gap call, no 2nd round (LLM only sees
    plan + synth, never a gap prompt)."""
    plan = json.dumps(["q1?"])
    synth = "# T\n## TL;DR\n\nClaim [^1]."
    llm = FakeLLM([plan, synth])  # exactly 2 calls; a 3rd would IndexError
    search = make_search({"q1?": [{"url": "https://arxiv.org/x", "title": "X", "snippet": ""}]})

    async def _extract(url: str):
        return {"ok": True, "url": url, "title": "X",
                "text": "content content. " * 40, "fetched_at": time.time()}

    report = await research_run("topic", llm_call=llm, search=search, extract=_extract)
    assert report.coverage["rounds"] == 1
    assert len(llm.calls) == 2  # plan + synth only


@pytest.mark.asyncio
async def test_research_run_semantic_scorer_reranks(monkeypatch) -> None:
    """When a semantic scorer is wired, a low-authority but semantically
    on-topic source can be lifted in relevance (blended into composite)."""
    plan = json.dumps(["q1?"])
    synth = "# T\n## TL;DR\n\nA [^1].\n\nB [^2]."
    llm = FakeLLM([plan, synth])
    search = make_search({"q1?": [
        {"url": "https://random-xyz.net/a", "title": "Topical", "snippet": ""},
        {"url": "https://arxiv.org/b", "title": "Offtopic", "snippet": ""},
    ]})

    async def _extract(url: str):
        return {"ok": True, "url": url, "title": url.rsplit("/", 1)[-1],
                "text": "some content here. " * 40, "fetched_at": time.time()}

    # Fake scorer: the random-xyz passage is highly relevant (1.0), arxiv low (0.0)
    def _sem(query, passages):
        return [1.0 if "some content" in p else 0.0 for p in passages][: len(passages)] \
            if False else [1.0, 0.0][: len(passages)]

    monkeypatch.setattr(r, "_SEMANTIC_SCORER", _sem)
    report = await research_run("topic", llm_call=llm, search=search, extract=_extract)
    # both kept; semantic ran without error and report is well-formed
    assert len(report.citations) == 2
    assert report.coverage["n_sources"] == 2


@pytest.mark.asyncio
async def test_research_run_drops_ai_generated_source() -> None:
    """自带'包含AI生成内容'声明的页面 → 被剔除,不进引用池。"""
    plan = json.dumps(["q1?"])
    synth = "# T\n## TL;DR\n\nClaim [^1]."
    llm = FakeLLM([plan, synth])
    search = make_search({"q1?": [
        {"url": "https://www.sohu.com/a/ai", "title": "AI repost", "snippet": ""},
        {"url": "https://arxiv.org/clean", "title": "Clean", "snippet": ""},
    ]})

    async def _extract(url: str):
        if "sohu" in url:
            return {"ok": True, "url": url, "title": "AI repost",
                    "text": "本文内容包含人工智能生成内容。" + "钠电池产量数据。" * 40,
                    "fetched_at": time.time()}
        return {"ok": True, "url": url, "title": "Clean",
                "text": "real research content about the topic. " * 40,
                "fetched_at": time.time()}

    report = await research_run("topic", llm_call=llm, search=search, extract=_extract)
    urls = [c.url for c in report.citations]
    assert "https://www.sohu.com/a/ai" not in urls  # AI 生成源被剔除
    assert "https://arxiv.org/clean" in urls
    assert any("dropped_ai_generated" in e for e in report.errors)


@pytest.mark.asyncio
async def test_research_run_drops_ai_flag_from_extract() -> None:
    """extract 阶段扫原始 HTML 置 payload['ai_generated']=True(正文已被
    trafilatura 剥掉声明,看不出来)→ 仍被剔除。"""
    plan = json.dumps(["q1?"])
    synth = "# T\n## TL;DR\n\nClaim [^1]."
    llm = FakeLLM([plan, synth])
    search = make_search({"q1?": [
        {"url": "https://www.sohu.com/a/clean-looking", "title": "S", "snippet": ""},
        {"url": "https://arxiv.org/ok", "title": "A", "snippet": ""},
    ]})

    async def _extract(url: str):
        # sohu 正文干净(声明被剥掉),但 extract 扫原始 HTML 标了 ai_generated
        if "sohu" in url:
            return {"ok": True, "url": url, "title": "S", "ai_generated": True,
                    "text": "钠电池产量数据很扎实。" * 40, "fetched_at": time.time()}
        return {"ok": True, "url": url, "title": "A",
                "text": "clean research content here. " * 40, "fetched_at": time.time()}

    report = await research_run("topic", llm_call=llm, search=search, extract=_extract)
    urls = [c.url for c in report.citations]
    assert "https://www.sohu.com/a/clean-looking" not in urls
    assert any("dropped_ai_generated" in e for e in report.errors)


@pytest.mark.asyncio
async def test_research_run_prunes_unused_citations() -> None:
    """正文只引用了 [^1],来源池里有 2 条 → 附录只列 [^1],废条目 [^2] 清掉。"""
    plan = json.dumps(["q1?", "q2?"])
    synth = "# T\n## TL;DR\n\n只引用第一个来源 [^1]，不引用第二个。"
    llm = FakeLLM([plan, synth])
    search = make_search({
        "q1?": [{"url": "https://arxiv.org/a", "title": "A", "snippet": ""}],
        "q2?": [{"url": "https://nature.com/b", "title": "B", "snippet": ""}],
    })

    async def _extract(url: str):
        return {"ok": True, "url": url, "title": url.rsplit("/", 1)[-1],
                "text": "research content about the topic here. " * 40,
                "fetched_at": time.time()}

    report = await research_run("topic", llm_call=llm, search=search, extract=_extract)
    # 只保留正文引用的 [^1]
    assert len(report.citations) == 1
    assert report.citations[0].n == 1
    # 附录里不出现废条目
    assert "[^2]:" not in report.report_md


def test_parse_rerank_scores():
    assert r._parse_rerank_scores('[{"id":1,"score":8},{"id":2,"score":3}]') == {1: 8.0, 2: 3.0}
    assert r._parse_rerank_scores('前言 [{"id":3,"score":9.5}] 后语') == {3: 9.5}
    assert r._parse_rerank_scores("not json") == {}
    assert r._parse_rerank_scores("") == {}


@pytest.mark.asyncio
async def test_research_run_llm_rerank_reorders(monkeypatch):
    """注入 rerank 桥(廉价模型 stub)→ 把低权威但被 rerank 判高分的源顶上来;
    coverage.reranker == 'llm'。"""
    plan = json.dumps(["q1?"])
    synth = "# T\n## TL;DR\n\nA [^1].\n\nB [^2]."
    llm = FakeLLM([plan, synth])  # 主 llm 只管 plan+synth(rerank 走独立桥)
    search = make_search({"q1?": [
        {"url": "https://random-xyz.net/topical", "title": "Topical", "snippet": ""},
        {"url": "https://arxiv.org/offtopic", "title": "Offtopic", "snippet": ""},
    ]})

    async def _extract(url: str):
        return {"ok": True, "url": url, "title": url.rsplit("/", 1)[-1],
                "text": "content about the subject here. " * 40, "fetched_at": time.time()}

    # rerank stub: 给 random-xyz(低权威)打高分,arxiv 打低分 → 应反转排序
    async def _rerank(prompt: str) -> str:
        # 解析出 candidate id(按出现顺序),给第一个 url(random)高分
        import re as _re
        ids = [int(x) for x in _re.findall(r"^(\d+) \|", prompt, _re.MULTILINE)]
        out = []
        for i in ids:
            # random-xyz 的段落标题是 topical → 在 prompt 里;简单按 id 给分
            out.append({"id": i, "score": 9 if i == _topical_id else 1})
        return json.dumps(out)

    # 先跑一次确定哪个 id 是 random-xyz —— 改用更稳的判定:给所有 id 中含'topical'
    async def _rerank2(prompt: str) -> str:
        import re as _re
        out = []
        for line in prompt.splitlines():
            m = _re.match(r"^(\d+) \| .* \| (.*?) \|", line)
            if m:
                cid = int(m.group(1)); title = m.group(2)
                out.append({"id": cid, "score": 9 if "topical" in title.lower() else 1})
        return json.dumps(out)

    monkeypatch.setattr(r, "_RERANK_LLM_CALL", _rerank2)
    report = await research_run("topic", llm_call=llm, search=search, extract=_extract)
    assert report.coverage["reranker"] == "llm"
    assert len(report.citations) == 2
    # rerank 把 topical(random-xyz)顶到 #1,尽管它域名权威更低
    assert report.citations[0].url == "https://random-xyz.net/topical"


@pytest.mark.asyncio
async def test_research_run_rerank_skipped_when_unwired(monkeypatch):
    """未注入 rerank 桥 → 跳过精排,coverage.reranker == 'off',主 llm 只被
    调用 plan+synth(不被 rerank 多消耗一次)。"""
    # 显式置 None: 别的测试 import main.py 可能把全局 _RERANK_LLM_CALL 设过(污染)。
    monkeypatch.setattr(r, "_RERANK_LLM_CALL", None)
    plan = json.dumps(["q1?"])
    synth = "# T\n## TL;DR\n\nClaim [^1]."
    llm = FakeLLM([plan, synth])  # 恰好 2 次;若 rerank 误触发会 IndexError
    search = make_search({"q1?": [{"url": "https://arxiv.org/x", "title": "X", "snippet": ""}]})

    async def _extract(url: str):
        return {"ok": True, "url": url, "title": "X",
                "text": "content content here. " * 40, "fetched_at": time.time()}

    report = await research_run("topic", llm_call=llm, search=search, extract=_extract)
    assert report.coverage["reranker"] == "off"
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_research_run_rerank_failure_marks_llm_failed(monkeypatch):
    """rerank 桥在但调用抛错 → coverage.reranker=='llm_failed'(不误标 llm),
    保留原打分照常出报告。"""
    plan = json.dumps(["q1?"])
    synth = "# T\n## TL;DR\n\nA [^1]."
    llm = FakeLLM([plan, synth])
    search = make_search({"q1?": [{"url": "https://arxiv.org/a", "title": "A", "snippet": ""}]})

    async def _extract(url: str):
        return {"ok": True, "url": url, "title": "A",
                "text": "content here. " * 40, "fetched_at": time.time()}

    async def _rerank_boom(prompt: str) -> str:
        raise RuntimeError("relay 500")

    monkeypatch.setattr(r, "_RERANK_LLM_CALL", _rerank_boom)
    report = await research_run("topic", llm_call=llm, search=search, extract=_extract)
    assert report.coverage["reranker"] == "llm_failed"
    assert any("rerank_llm" in e for e in report.errors)
    assert len(report.citations) == 1  # 报告照常


@pytest.mark.asyncio
async def test_research_run_rerank_low_coverage_noop(monkeypatch):
    """rerank 只给极少 id 打分(低覆盖)→ 整次 no-op,标 llm_failed。"""
    plan = json.dumps(["q1?", "q2?"])
    synth = "# T\n## TL;DR\n\nA [^1]. B [^2]."
    llm = FakeLLM([plan, synth])
    search = make_search({
        "q1?": [{"url": "https://arxiv.org/a", "title": "A", "snippet": ""}],
        "q2?": [{"url": "https://nature.com/b", "title": "B", "snippet": ""}],
    })

    async def _extract(url: str):
        return {"ok": True, "url": url, "title": url.rsplit("/", 1)[-1],
                "text": "content here. " * 40, "fetched_at": time.time()}

    async def _rerank_partial(prompt: str) -> str:
        return json.dumps([])  # 一个都没打 → 0 覆盖 → no-op

    monkeypatch.setattr(r, "_RERANK_LLM_CALL", _rerank_partial)
    report = await research_run("topic", llm_call=llm, search=search, extract=_extract)
    assert report.coverage["reranker"] == "llm_failed"
    assert any("rerank_low_coverage" in e for e in report.errors)


def test_rerank_mode_config(monkeypatch):
    """_rerank_mode: off/llm/local/非法/空白 的健壮解析。"""
    import types

    def _set(val):
        fake = types.SimpleNamespace(config=types.SimpleNamespace(raw={"research": {"reranker": val}}))
        monkeypatch.setitem(__import__("sys").modules, "config", fake)
        r._RESEARCH_RAW_CACHE = None   # 清缓存,让 _research_raw 重读 fake config

    _set("off");      assert r._rerank_mode() == "off"
    _set(" OFF ");    assert r._rerank_mode() == "off"   # strip + 大小写
    _set("llm");      assert r._rerank_mode() == "llm"
    _set("local");    assert r._rerank_mode() == "llm"   # 暂退化
    _set("garbage");  assert r._rerank_mode() == "llm"   # 非法 → 保守 llm
    _set("");         assert r._rerank_mode() == "llm"


def test_is_loopback_url():
    """localhost 守卫: 解析 hostname + ipaddress,覆盖 ::1/127网段/大小写,
    不误伤含 localhost 子串的远端域名。"""
    assert r._is_loopback_url("http://localhost:11434") is True
    assert r._is_loopback_url("http://127.0.0.1:8000/v1") is True
    assert r._is_loopback_url("http://127.0.0.2:8000") is True   # 127/8 整段
    assert r._is_loopback_url("http://[::1]:8000/v1") is True
    assert r._is_loopback_url("http://LOCALHOST:1234") is True   # 大小写
    assert r._is_loopback_url("https://relay.example.com/v1") is False  # 远端 relay
    assert r._is_loopback_url("https://my-localhost-cdn.com/v1") is False  # 不误伤
    assert r._is_loopback_url("") is False


def test_parse_rerank_scores_dedup_and_types():
    # 重复 id → 后者覆盖;非法项跳过
    assert r._parse_rerank_scores('[{"id":1,"score":5},{"id":1,"score":8}]') == {1: 8.0}
    assert r._parse_rerank_scores('[{"id":2,"score":"x"},{"id":3,"score":7}]') == {3: 7.0}


@pytest.mark.asyncio
async def test_research_run_rerank_timeout_marks_failed(monkeypatch):
    """rerank 调用超时 → wait_for 触发,标 llm_failed + rerank_timeout,报告照常。"""
    monkeypatch.setattr(r, "_RERANK_TIMEOUT", 0.05)
    plan = json.dumps(["q1?"])
    synth = "# T\n## TL;DR\n\nA [^1]."
    llm = FakeLLM([plan, synth])
    search = make_search({"q1?": [{"url": "https://arxiv.org/a", "title": "A", "snippet": ""}]})

    async def _extract(url: str):
        return {"ok": True, "url": url, "title": "A",
                "text": "content here. " * 40, "fetched_at": time.time()}

    async def _rerank_slow(prompt: str) -> str:
        await asyncio.sleep(1.0)  # > _RERANK_TIMEOUT
        return "[]"

    monkeypatch.setattr(r, "_RERANK_LLM_CALL", _rerank_slow)
    report = await research_run("topic", llm_call=llm, search=search, extract=_extract)
    assert report.coverage["reranker"] == "llm_failed"
    assert any("rerank_timeout" in e for e in report.errors)
    assert len(report.citations) == 1


@pytest.mark.asyncio
async def test_research_run_drops_mojibake_source() -> None:
    """抽出整段乱码的源 → 被剔除,不进引用池(codex 抓到的 [^11] 类问题)。"""
    plan = json.dumps(["q1?"])
    synth = "# T\n## TL;DR\n\nClaim [^1]."
    llm = FakeLLM([plan, synth])
    search = make_search({"q1?": [
        {"url": "https://bad-encoding.cn/x", "title": "乱码", "snippet": ""},
        {"url": "https://arxiv.org/ok", "title": "Clean", "snippet": ""},
    ]})

    async def _extract(url: str):
        if "bad-encoding" in url:
            return {"ok": True, "url": url, "title": "garbled",
                    "text": "锛阢绅钆婅皖銮" * 60,
                    "fetched_at": time.time()}
        return {"ok": True, "url": url, "title": "Clean",
                "text": "clean readable research content here. " * 40, "fetched_at": time.time()}

    report = await research_run("topic", llm_call=llm, search=search, extract=_extract)
    urls = [c.url for c in report.citations]
    assert "https://bad-encoding.cn/x" not in urls
    assert any("dropped_mojibake" in e for e in report.errors)


# --- P1-3 Jina Reader 二级抓取 ---

def test_parse_jina():
    body = "Title: 我的标题\nURL Source: http://x\nMarkdown Content:\n这是正文内容。"
    out = r._parse_jina(body)
    assert out["title"] == "我的标题"
    assert out["text"] == "这是正文内容。"
    # 无结构头的纯 markdown
    out2 = r._parse_jina("# 纯Markdown标题\n一些内容在这里。")
    assert "纯Markdown标题" in out2["text"]


@pytest.mark.asyncio
async def test_default_extract_jina_fallback(monkeypatch):
    """trafilatura 在 JS 空壳上抽空 + Jina 显式开 → 降级 Jina 救回正文,extractor='jina'。"""
    monkeypatch.setattr(r, "_jina_enabled", lambda: True)  # opt-in 默认关,测试显式开

    class _Resp:
        def __init__(self, text):
            self.text = text
            self.url = "x"

        def raise_for_status(self):
            return None

    class _FakeClient:
        async def get(self, url, **kw):
            if url.startswith("https://r.jina.ai/"):
                return _Resp("Title: 真实标题\nURL Source: x\nMarkdown Content:\n"
                             + ("这是 JS 渲染后救回的真实正文内容。" * 40))
            # JS 空壳: trafilatura 抽不到正文
            return _Resp("<html><body><div id='app'></div><script>render()</script></body></html>")

        async def aclose(self):
            return None

    out = await r.default_extract("https://spa-site.com/article", client=_FakeClient())
    assert out["ok"] is True
    assert out["extractor"] == "jina"
    assert "救回的真实正文" in out["text"]


@pytest.mark.asyncio
async def test_default_extract_trafilatura_enough_no_jina():
    """trafilatura 抽到够长正文 → 不调 Jina(extractor='trafilatura')。"""
    jina_called = {"n": 0}

    class _Resp:
        def __init__(self, text):
            self.text = text
            self.url = "x"

        def raise_for_status(self):
            return None

    article_html = ("<html><head><title>好文</title></head><body><article><p>"
                    + ("这是一篇内容充实的静态文章正文。" * 60)
                    + "</p></article></body></html>")

    class _FakeClient:
        async def get(self, url, **kw):
            if url.startswith("https://r.jina.ai/"):
                jina_called["n"] += 1
                return _Resp("Title: x\nMarkdown Content:\nshould not be used")
            return _Resp(article_html)

        async def aclose(self):
            return None

    out = await r.default_extract("https://static-site.com/a", client=_FakeClient())
    assert out["ok"] is True
    assert out["extractor"] == "trafilatura"
    assert jina_called["n"] == 0  # 没调 Jina


@pytest.mark.asyncio
async def test_default_extract_jina_default_off():
    """Jina 默认关(opt-in): JS 空壳抽空且未开 Jina → 不调 r.jina.ai,返回失败。"""
    jina_hit = {"n": 0}

    class _Resp:
        def __init__(self, text):
            self.text = text
            self.url = "x"

        def raise_for_status(self):
            return None

    class _FakeClient:
        async def get(self, url, **kw):
            if url.startswith("https://r.jina.ai/"):
                jina_hit["n"] += 1
                return _Resp("Title: x Markdown Content: " + ("救回" * 200))
            return _Resp("<html><body><div id='app'></div></body></html>")  # JS 空壳

        async def aclose(self):
            return None

    out = await r.default_extract("https://spa.com/x", client=_FakeClient())
    assert jina_hit["n"] == 0          # 默认关 → 没调 Jina
    assert out["ok"] is False          # trafilatura 抽空 + Jina 关 → 失败


# --- JS 渲染兜底 (Option C: cdp-edge) ---

# >20KB 的 JS 空壳(过双闸①): head 有 title,body 是空 div + 一大段 script(trafilatura 抽不到正文)
_JS_SHELL_BIG = ("<html><head><title>JS 站</title></head><body><div id='app'></div>"
                 "<script>/*" + ("x" * 21000) + "*/render()</script></body></html>")
# <20KB 的小空壳(不过双闸①)
_JS_SHELL_SMALL = "<html><head><title>小</title></head><body><div id='app'></div></body></html>"
_RENDERED_LONG = ("<html><body><article><p>"
                  + ("这是 JS 渲染后救回的真实正文。" * 60) + "</p></article></body></html>")


def _fake_client_factory(jina_counter=None):
    class _Resp:
        def __init__(self, text):
            self.text = text
            self.url = "x"

        def raise_for_status(self):
            return None

    class _FakeClient:
        def __init__(self, shell):
            self._shell = shell

        async def get(self, url, **kw):
            if url.startswith("https://r.jina.ai/"):
                if jina_counter is not None:
                    jina_counter["n"] += 1
                return _Resp("Title: x\nMarkdown Content:\n" + ("jina救回" * 80))
            return _Resp(self._shell)

        async def aclose(self):
            return None

    return _Resp, _FakeClient


@pytest.mark.asyncio
async def test_default_extract_js_render_cdp_edge(monkeypatch):
    """js_render 开 + engine=cdp-edge: 大空壳 → 渲染救回正文,extractor='cdp-edge',且不调 jina(去重)。"""
    monkeypatch.setattr(r, "_js_render_enabled", lambda: True)
    monkeypatch.setattr(r, "_js_render_engine", lambda: "cdp-edge")
    import deskpet.tools.research_cdp_edge as ce_mod
    monkeypatch.setattr(r, "_jina_enabled", lambda: True)   # 开着也应被去重跳过
    jina_counter = {"n": 0}

    async def _fake_render(url, *, timeout=20.0):
        return _RENDERED_LONG

    monkeypatch.setattr(ce_mod, "cdp_edge_render", _fake_render)
    _Resp, _FakeClient = _fake_client_factory(jina_counter)
    out = await r.default_extract("https://spa.com/x", client=_FakeClient(_JS_SHELL_BIG))
    assert out["ok"] is True
    assert out["extractor"] == "cdp-edge"
    assert "渲染后救回的真实正文" in out["text"]
    assert jina_counter["n"] == 0          # 本地渲染命中 → 跳过 jina(R6 去重)


@pytest.mark.asyncio
async def test_default_extract_js_render_small_html_no_trigger(monkeypatch):
    """双闸①: trafilatura 短但原始 HTML <20KB → 不触发渲染(防正常短页误触发)。"""
    monkeypatch.setattr(r, "_js_render_enabled", lambda: True)
    monkeypatch.setattr(r, "_js_render_engine", lambda: "cdp-edge")
    monkeypatch.setattr(r, "_jina_enabled", lambda: False)
    import deskpet.tools.research_cdp_edge as ce_mod
    called = {"n": 0}

    async def _fake_render(url, *, timeout=20.0):
        called["n"] += 1
        return _RENDERED_LONG

    monkeypatch.setattr(ce_mod, "cdp_edge_render", _fake_render)
    _Resp, _FakeClient = _fake_client_factory()
    out = await r.default_extract("https://spa.com/x", client=_FakeClient(_JS_SHELL_SMALL))
    assert called["n"] == 0   # 小 HTML 不过双闸① → 没调渲染


@pytest.mark.asyncio
async def test_default_extract_js_render_off_no_call(monkeypatch):
    """js_render 关(默认): 大空壳也不调渲染,extractor 保持 trafilatura(flag-off 行为)。"""
    monkeypatch.setattr(r, "_js_render_enabled", lambda: False)
    monkeypatch.setattr(r, "_jina_enabled", lambda: False)
    import deskpet.tools.research_cdp_edge as ce_mod
    called = {"n": 0}

    async def _fake_render(url, *, timeout=20.0):
        called["n"] += 1
        return _RENDERED_LONG

    monkeypatch.setattr(ce_mod, "cdp_edge_render", _fake_render)
    _Resp, _FakeClient = _fake_client_factory()
    out = await r.default_extract("https://spa.com/x", client=_FakeClient(_JS_SHELL_BIG))
    assert called["n"] == 0   # flag off → 没调渲染


@pytest.mark.asyncio
async def test_default_extract_js_render_fail_falls_to_jina(monkeypatch):
    """渲染返 None → 回落 jina(本地渲染未命中,extractor 仍 trafilatura → jina 兜底)。"""
    monkeypatch.setattr(r, "_js_render_enabled", lambda: True)
    monkeypatch.setattr(r, "_js_render_engine", lambda: "cdp-edge")
    monkeypatch.setattr(r, "_jina_enabled", lambda: True)
    import deskpet.tools.research_cdp_edge as ce_mod
    jina_counter = {"n": 0}

    async def _fake_render(url, *, timeout=20.0):
        return None   # 渲染失败

    monkeypatch.setattr(ce_mod, "cdp_edge_render", _fake_render)
    _Resp, _FakeClient = _fake_client_factory(jina_counter)
    out = await r.default_extract("https://spa.com/x", client=_FakeClient(_JS_SHELL_BIG))
    assert jina_counter["n"] == 1          # 渲染没命中 → 试 jina
    assert out["extractor"] == "jina"


@pytest.mark.asyncio
async def test_js_render_budget_cap(monkeypatch):
    """单轮触发计数上限: 超过 _JS_RENDER_MAX_PER_RUN 次后不再调渲染。"""
    monkeypatch.setattr(r, "_js_render_enabled", lambda: True)
    monkeypatch.setattr(r, "_js_render_engine", lambda: "cdp-edge")
    monkeypatch.setattr(r, "_jina_enabled", lambda: False)
    import deskpet.tools.research_cdp_edge as ce_mod
    called = {"n": 0}

    async def _fake_render(url, *, timeout=20.0):
        called["n"] += 1
        return None   # 返 None,不替换,纯计触发次数

    monkeypatch.setattr(ce_mod, "cdp_edge_render", _fake_render)
    r._reset_js_render_budget()
    _Resp, _FakeClient = _fake_client_factory()
    for i in range(r._JS_RENDER_MAX_PER_RUN + 3):
        await r.default_extract(f"https://spa.com/{i}", client=_FakeClient(_JS_SHELL_BIG))
    assert called["n"] == r._JS_RENDER_MAX_PER_RUN   # 触发次数封顶


@pytest.mark.asyncio
async def test_default_extract_js_render_engine_crawl4ai(monkeypatch):
    """engine=crawl4ai → 路由到 crawl4ai 适配器(本期 dev 档,mock 之)。"""
    monkeypatch.setattr(r, "_js_render_enabled", lambda: True)
    monkeypatch.setattr(r, "_js_render_engine", lambda: "crawl4ai")
    monkeypatch.setattr(r, "_jina_enabled", lambda: False)
    import sys as _sys, types as _types
    fake_mod = _types.ModuleType("deskpet.tools.research_crawl4ai")

    async def _fake_c4(url, *, timeout=20.0):
        return {"html": _RENDERED_LONG}

    fake_mod.crawl4ai_extract = _fake_c4
    monkeypatch.setitem(_sys.modules, "deskpet.tools.research_crawl4ai", fake_mod)
    _Resp, _FakeClient = _fake_client_factory()
    out = await r.default_extract("https://spa.com/x", client=_FakeClient(_JS_SHELL_BIG))
    assert out["extractor"] == "crawl4ai"
    assert "渲染后救回的真实正文" in out["text"]


@pytest.mark.asyncio
async def test_js_render_ai_generated_scans_rendered_html(monkeypatch):
    """渲染命中后, ai_generated 须扫【渲染后 HTML】: 原始空壳无 AI 声明、渲染后正文含声明
    → out['ai_generated'] 为 True(plan WI-3:渲染路径锚点不同,须分别覆盖)。"""
    monkeypatch.setattr(r, "_js_render_enabled", lambda: True)
    monkeypatch.setattr(r, "_js_render_engine", lambda: "cdp-edge")
    monkeypatch.setattr(r, "_jina_enabled", lambda: False)
    import deskpet.tools.research_cdp_edge as ce_mod
    # 渲染后 HTML: 长正文 + AI 生成声明(原始空壳里没有这句)
    rendered_with_ai = ("<html><body><article><p>"
                        + ("这是 JS 渲染后救回的真实正文。" * 60)
                        + "本文包含人工智能生成内容。</p></article></body></html>")

    async def _fake_render(url, *, timeout=20.0):
        return rendered_with_ai

    monkeypatch.setattr(ce_mod, "cdp_edge_render", _fake_render)
    _Resp, _FakeClient = _fake_client_factory()
    # 原始大空壳不含 AI 声明
    assert "人工智能生成" not in _JS_SHELL_BIG
    out = await r.default_extract("https://spa.com/x", client=_FakeClient(_JS_SHELL_BIG))
    assert out["extractor"] == "cdp-edge"
    assert out["ai_generated"] is True   # 扫的是渲染后 HTML(含声明),不是原始空壳


@pytest.mark.asyncio
async def test_js_render_ai_generated_not_in_rendered(monkeypatch):
    """对照: 原始空壳含 AI 声明但渲染后正文不含 → 命中渲染后 ai_generated 为 False(证明扫的是渲染后)。"""
    monkeypatch.setattr(r, "_js_render_enabled", lambda: True)
    monkeypatch.setattr(r, "_js_render_engine", lambda: "cdp-edge")
    monkeypatch.setattr(r, "_jina_enabled", lambda: False)
    import deskpet.tools.research_cdp_edge as ce_mod
    shell_with_ai = ("<html><head><title>站</title></head><body>"
                     "<!-- 本文包含人工智能生成内容 --><div id='app'></div>"
                     "<script>/*" + ("x" * 21000) + "*/</script></body></html>")

    async def _fake_render(url, *, timeout=20.0):
        return _RENDERED_LONG   # 干净正文,无 AI 声明

    monkeypatch.setattr(ce_mod, "cdp_edge_render", _fake_render)
    _Resp, _FakeClient = _fake_client_factory()
    out = await r.default_extract("https://spa.com/x", client=_FakeClient(shell_with_ai))
    assert out["extractor"] == "cdp-edge"
    assert out["ai_generated"] is False   # 渲染后 HTML 无声明 → False(没去扫原始空壳)


# --- P1-2 site: 定向官方域 ---

def test_site_directive_for():
    assert r._site_directive_for("某上市公司2025年财报营收") == "site:cninfo.com.cn"
    assert r._site_directive_for("新能源汽车产业政策和监管办法") == "site:gov.cn"
    assert r._site_directive_for("钠离子电池国家标准技术规范") == "site:gov.cn"
    assert r._site_directive_for("transformer 算法原理 论文 arxiv") == "site:arxiv.org"
    assert r._site_directive_for("今天天气怎么样") is None  # 不命中 → 不定向


@pytest.mark.asyncio
async def test_research_run_site_directed_search(monkeypatch):
    """政策类子问题 → 额外发一条 site:gov.cn 定向搜;命中的 gov.cn 源进引用池。"""
    monkeypatch.setattr(r, "_RERANK_LLM_CALL", None)  # 隔离 rerank 污染
    plan = json.dumps(["新能源汽车补贴政策有哪些?"])
    synth = "# T\n## TL;DR\n\n据官方 [^1]。"
    llm = FakeLLM([plan, synth])
    searched = []

    async def _search(q, *, max_results=5):
        searched.append(q)
        if "site:gov.cn" in q:
            return [{"url": "https://www.gov.cn/zhengce/x", "title": "补贴政策", "snippet": ""}]
        return [{"url": "https://auto-news.com/y", "title": "新闻", "snippet": ""}]

    async def _extract(url):
        return {"ok": True, "url": url, "title": url.rsplit("/", 1)[-1],
                "text": "新能源汽车补贴政策内容详细说明在这里。" * 30, "fetched_at": time.time()}

    report = await research_run("topic", llm_call=llm, search=_search, extract=_extract)
    # 确实发了 site:gov.cn 定向搜
    assert any("site:gov.cn" in q for q in searched)
    # gov.cn 官方源进了引用池
    assert any("gov.cn" in c.url for c in report.citations)


@pytest.mark.asyncio
async def test_research_run_site_directed_off(monkeypatch):
    """site_directed 关 → 不发 site: 定向搜。"""
    monkeypatch.setattr(r, "_RERANK_LLM_CALL", None)
    monkeypatch.setattr(r, "_site_directed_enabled", lambda: False)
    plan = json.dumps(["新能源汽车政策监管办法?"])
    synth = "# T\n## TL;DR\n\nA [^1]."
    llm = FakeLLM([plan, synth])
    searched = []

    async def _search(q, *, max_results=5):
        searched.append(q)
        return [{"url": "https://x.com/a", "title": "A", "snippet": ""}]

    async def _extract(url):
        return {"ok": True, "url": url, "title": "A",
                "text": "content here. " * 40, "fetched_at": time.time()}

    await research_run("topic", llm_call=llm, search=_search, extract=_extract)
    assert not any("site:" in q for q in searched)  # 关了 → 无 site: 定向


# --- P2 query expansion (multi-query / HyDE) ---

@pytest.mark.asyncio
async def test_research_run_query_expansion(monkeypatch):
    """开启 query_expansion → plan 后多一次 LLM 产额外 query,这些 query 被搜。"""
    monkeypatch.setattr(r, "_query_expansion_enabled", lambda: True)
    monkeypatch.setattr(r, "_RERANK_LLM_CALL", None)
    plan = json.dumps(["q1?"])
    expansion = json.dumps(["扩展查询A", "扩展查询B"])  # multi-query 改写
    synth = "# T\n## TL;DR\n\nA [^1]."
    llm = FakeLLM([plan, expansion, synth])  # plan → expansion → synth
    searched = []

    async def _search(q, *, max_results=5):
        searched.append(q)
        return [{"url": "https://arxiv.org/a", "title": "A", "snippet": ""}]

    async def _extract(url):
        return {"ok": True, "url": url, "title": "A",
                "text": "content here. " * 40, "fetched_at": time.time()}

    await research_run("topic", llm_call=llm, search=_search, extract=_extract)
    assert "扩展查询A" in searched and "扩展查询B" in searched


# --- P2 direct sources (cninfo / openstd) ---

@pytest.mark.asyncio
async def test_research_run_direct_source_cninfo(monkeypatch):
    """子问题谈财报 → 巨潮直连源进引用池(高权威)。"""
    monkeypatch.setattr(r, "_direct_sources_enabled", lambda: True)
    monkeypatch.setattr(r, "_RERANK_LLM_CALL", None)
    import deskpet.tools.research_sources as rs_mod

    async def _fake_cninfo(keyword, *, max_results=3, client=None):
        return [{"ok": True, "url": "http://static.cninfo.com.cn/x/123.PDF",
                 "title": "宁德时代 2025年年度报告",
                 "text": "宁德时代2025年营收与净利润等关键财务数据正文。" * 30,
                 "fetched_at": time.time(), "source": "cninfo"}]

    monkeypatch.setattr(rs_mod, "cninfo_search", _fake_cninfo)
    plan = json.dumps(["宁德时代2025年财报营收如何?"])
    synth = "# T\n## TL;DR\n\n据公告 [^1]."
    llm = FakeLLM([plan, synth])

    async def _search(q, *, max_results=5):
        return [{"url": "https://news.com/y", "title": "新闻", "snippet": ""}]

    async def _extract(url):
        return {"ok": True, "url": url, "title": "新闻",
                "text": "一般新闻内容。" * 40, "fetched_at": time.time()}

    report = await research_run("topic", llm_call=llm, search=_search, extract=_extract)
    assert any("cninfo.com.cn" in c.url for c in report.citations)


@pytest.mark.asyncio
async def test_research_run_direct_source_disabled(monkeypatch):
    """direct_sources 关 → 不调直连源(即使子问题谈财报)。"""
    monkeypatch.setattr(r, "_direct_sources_enabled", lambda: False)
    monkeypatch.setattr(r, "_RERANK_LLM_CALL", None)
    import deskpet.tools.research_sources as rs_mod
    called = {"n": 0}

    async def _fake_cninfo(keyword, *, max_results=3, client=None):
        called["n"] += 1
        return []

    monkeypatch.setattr(rs_mod, "cninfo_search", _fake_cninfo)
    plan = json.dumps(["某公司财报营收?"])
    synth = "# T\n## TL;DR\n\nA [^1]."
    llm = FakeLLM([plan, synth])

    async def _search(q, *, max_results=5):
        return [{"url": "https://x.com/a", "title": "A", "snippet": ""}]

    async def _extract(url):
        return {"ok": True, "url": url, "title": "A",
                "text": "content. " * 40, "fetched_at": time.time()}

    await research_run("topic", llm_call=llm, search=_search, extract=_extract)
    assert called["n"] == 0
