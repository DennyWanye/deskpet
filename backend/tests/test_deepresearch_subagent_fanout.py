# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any

import pytest

from deskpet.tools import research_tools as r
from deskpet.tools.research_tools import Citation, ResearchReport


class FakeLLM:
    def __init__(self, responses: list[str | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class RefAwareSynthLLM:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        refs = sorted({int(n) for n in re.findall(r"(?m)^\[\^(\d+)\]:", prompt)})
        ref_text = "".join(f"[^{n}]" for n in refs) or ""
        return (
            "# Fanout report\n\n"
            f"## TL;DR\n\nCombined fanout evidence {ref_text}.\n\n"
            f"## Analysis\n\nEvery completed subreport is represented {ref_text}.\n"
        )


class DirectScheduler:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run(self, *, kind, run_id, task_id, parent_sid, coro_factory):
        self.calls.append({
            "kind": kind,
            "run_id": run_id,
            "task_id": task_id,
            "parent_sid": parent_sid,
        })
        return await coro_factory()


@pytest.fixture(autouse=True)
def _isolate_research_runtime(monkeypatch):
    monkeypatch.setattr(r, "_query_expansion_enabled", lambda: False)
    monkeypatch.setattr(r, "_direct_sources_enabled", lambda: False)
    monkeypatch.setattr(r, "_site_directed_enabled", lambda: False)
    monkeypatch.setattr(r, "_js_render_enabled", lambda: False)
    monkeypatch.setattr(r, "_rerank_mode", lambda: "off")
    monkeypatch.setattr(r, "_SEMANTIC_SCORER", None)
    r._RESEARCH_RAW_CACHE = None
    r.set_subagent_scheduler(None)
    yield
    r._RESEARCH_RAW_CACHE = None
    r.set_subagent_scheduler(None)


def _citation(n: int, url: str, title: str | None = None) -> Citation:
    return Citation(
        n=n,
        url=url,
        title=title or f"source {n}",
        snippet=f"snippet {n}",
        fetched_at=float(n),
        authority=1.0,
    )


def _sub_report(question: str, idx: int, *, url: str | None = None) -> ResearchReport:
    citation = _citation(1, url or f"https://example.com/{idx}", f"title {idx}")
    return ResearchReport(
        topic=question,
        summary=f"summary {idx}",
        report_md=(
            f"# {question}\n\n"
            f"## TL;DR\n\nFinding {idx} [^1].\n\n"
            f"## Detail\n\nMore evidence {idx} [^1].\n"
        ),
        citations=[citation],
        sub_questions=[question],
        coverage={"n_sources": 1, "n_domains": 1, "mode": "standard"},
        errors=[],
    )


def _make_search(canned: dict[str, list[dict[str, Any]]]):
    async def _search(query: str, *, max_results: int = 5):
        return list(canned.get(query, []))[:max_results]

    return _search


def _make_extract(canned: dict[str, dict[str, Any]]):
    async def _extract(url: str):
        return canned.get(url, {"ok": False, "error": "missing fixture", "url": url})

    return _extract


async def _run_flat_candidate(
    *,
    llm: FakeLLM,
    scheduler=None,
    depth: int = 0,
) -> ResearchReport:
    search = _make_search({
        "q1?": [{"url": "https://a.example/doc", "title": "A", "snippet": ""}],
        "q2?": [{"url": "https://b.example/doc", "title": "B", "snippet": ""}],
    })
    extract = _make_extract({
        "https://a.example/doc": {
            "ok": True,
            "url": "https://a.example/doc",
            "title": "A",
            "text": "alpha evidence about topic. " * 40,
            "fetched_at": 1.0,
        },
        "https://b.example/doc": {
            "ok": True,
            "url": "https://b.example/doc",
            "title": "B",
            "text": "beta evidence about topic. " * 40,
            "fetched_at": 2.0,
        },
    })
    return await r.deepresearch(
        "topic",
        llm_call=llm,
        search=search,
        extract=extract,
        max_sub_questions=2,
        scheduler=scheduler,
        _depth=depth,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("plan", "scheduler", "depth", "fanout_enabled"),
    [
        (json.dumps(["q1?", "q2?"]), None, 0, True),
        (json.dumps(["q1?", "q2?"]), DirectScheduler(), 1, True),
        (json.dumps(["q1?"]), DirectScheduler(), 0, True),
        (json.dumps(["q1?", "q2?"]), DirectScheduler(), 0, False),
    ],
)
async def test_tg1_fanout_off_branches_stay_on_flat_path(
    monkeypatch, plan, scheduler, depth, fanout_enabled
) -> None:
    async def _sentinel_fanout(**kwargs):
        raise AssertionError("_run_subagent_fanout must not be called")

    monkeypatch.setattr(r, "_run_subagent_fanout", _sentinel_fanout)
    monkeypatch.setattr(r, "_fanout_enabled", lambda: fanout_enabled)
    synth = (
        "# Flat report\n\n"
        "## TL;DR\n\nFlat summary [^1].\n\n"
        "## Analysis\n\nFlat evidence [^1].\n"
    )
    llm = FakeLLM([plan, synth])

    report = await _run_flat_candidate(llm=llm, scheduler=scheduler, depth=depth)

    assert report.summary.startswith("Flat summary")
    assert report.coverage["mode"] == "standard"
    assert "subagent_fanout" not in report.coverage
    assert report.citations
    assert "[^1]" in report.report_md


@pytest.mark.asyncio
async def test_tg2_fanout_runs_subreports_depth_one_and_merges(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    async def _inner(topic: str, **kwargs):
        calls.append({"topic": topic, **kwargs})
        return _sub_report(topic, len(calls))

    monkeypatch.setattr(r, "deepresearch", _inner)
    monkeypatch.setattr(r, "_fanout_concurrency", lambda: 2)
    scheduler = DirectScheduler()

    report = await r._run_subagent_fanout(
        topic="outer topic",
        sub_questions=["q1?", "q2?", "q3?"],
        llm_call=RefAwareSynthLLM(),
        search=_make_search({}),
        extract=_make_extract({}),
        scheduler=scheduler,
        parent_sid="sid-1",
        mode="standard",
        user_request="outer topic",
        errors=[],
        route={},
    )

    obs = report.coverage["subagent_fanout"]
    assert report.coverage["mode"] == "fanout"
    assert obs["n_subagents"] == 3
    assert obs["n_completed"] == 3
    assert obs["n_failed"] == 0
    assert obs["waves"] == 2
    assert report.summary
    assert len(report.citations) == 3
    assert len({c.n for c in report.citations}) == 3
    assert len({r._norm_url(c.url) for c in report.citations}) == 3
    assert [call["topic"] for call in calls] == ["q1?", "q2?", "q3?"]
    assert all(call["scheduler"] is None for call in calls)
    assert [call["user_request"] for call in calls] == ["q1?", "q2?", "q3?"]
    assert all(call["_depth"] == 1 for call in calls)
    assert all(call["max_sub_questions"] == 1 for call in calls)
    assert all(call["skip_plan"] is True for call in calls)
    assert len(scheduler.calls) == 3
    assert {call["kind"] for call in scheduler.calls} == {"research"}


@pytest.mark.asyncio
async def test_tg2_fanout_isolates_one_failed_subagent(monkeypatch) -> None:
    async def _inner(topic: str, **kwargs):
        if topic == "q2?":
            raise RuntimeError("boom")
        return _sub_report(topic, 1 if topic == "q1?" else 3)

    monkeypatch.setattr(r, "deepresearch", _inner)
    monkeypatch.setattr(r, "_fanout_concurrency", lambda: 2)
    errors: list[str] = []

    report = await r._run_subagent_fanout(
        topic="outer topic",
        sub_questions=["q1?", "q2?", "q3?"],
        llm_call=RefAwareSynthLLM(),
        search=_make_search({}),
        extract=_make_extract({}),
        scheduler=DirectScheduler(),
        parent_sid="sid-1",
        mode="standard",
        user_request="outer topic",
        errors=errors,
        route={},
    )

    obs = report.coverage["subagent_fanout"]
    assert obs["n_completed"] == 2
    assert obs["n_failed"] == 1
    assert len(report.citations) == 2
    assert any("q2?" in e and "boom" in e for e in report.errors)


@pytest.mark.asyncio
async def test_tg2_fanout_all_failures_returns_no_results_report(monkeypatch) -> None:
    async def _inner(topic: str, **kwargs):
        raise RuntimeError(f"failed {topic}")

    monkeypatch.setattr(r, "deepresearch", _inner)
    monkeypatch.setattr(r, "_fanout_concurrency", lambda: 2)

    report = await r._run_subagent_fanout(
        topic="all fail",
        sub_questions=["q1?", "q2?"],
        llm_call=RefAwareSynthLLM(),
        search=_make_search({}),
        extract=_make_extract({}),
        scheduler=DirectScheduler(),
        parent_sid="sid-1",
        mode="standard",
        user_request="all fail",
        errors=[],
        route={},
    )

    obs = report.coverage["subagent_fanout"]
    assert isinstance(report.summary, str)
    assert report.citations == []
    assert obs["n_subagents"] == 2
    assert obs["n_completed"] == 0
    assert obs["n_failed"] == 2
    assert report.coverage["n_sources"] == 0
    assert "# all fail" in report.report_md


@pytest.mark.asyncio
@pytest.mark.parametrize(("conc", "expected_cap"), [(2, 10), (1, 5)])
async def test_tg2_fanout_caps_subquestions_and_preserves_budget(
    monkeypatch, conc, expected_cap
) -> None:
    async def _inner(topic: str, **kwargs):
        idx = int(topic.removeprefix("q").removesuffix("?"))
        return _sub_report(topic, idx)

    monkeypatch.setattr(r, "deepresearch", _inner)
    monkeypatch.setattr(r, "_fanout_concurrency", lambda: conc)
    monkeypatch.setattr(r, "_fanout_max_subquestions", lambda: 99)
    sub_questions = [f"q{i}?" for i in range(20)]

    report = await r._run_subagent_fanout(
        topic="capped topic",
        sub_questions=sub_questions,
        llm_call=RefAwareSynthLLM(),
        search=_make_search({}),
        extract=_make_extract({}),
        scheduler=DirectScheduler(),
        parent_sid="sid-1",
        mode="standard",
        user_request="capped topic",
        errors=[],
        route={},
    )

    obs = report.coverage["subagent_fanout"]
    assert obs["n_subagents"] == expected_cap
    assert obs["n_completed"] == expected_cap
    assert obs["waves"] == 5
    assert any("fanout_dropped_subquestions" in e for e in report.errors)
    assert obs["per_subrun_timeout_s"] >= r._MIN_SUBRUN
    assert (
        obs["waves"] * obs["per_subrun_timeout_s"] + r._FANOUT_OUTER_RESERVE
        <= r._DEEPRESEARCH_TOOL_TIMEOUT
    )


def test_tg3_merge_subreport_citations_dedupes_urls_and_maps_refs() -> None:
    report_a = ResearchReport(
        topic="a",
        summary="",
        report_md="A [^1][^2]",
        citations=[
            _citation(1, "https://dup.example/path/?utm=1", "dup"),
            _citation(2, "https://unique-a.example/path", "a"),
        ],
        sub_questions=["a"],
        coverage={},
    )
    report_b = ResearchReport(
        topic="b",
        summary="",
        report_md="B [^1][^2]",
        citations=[
            _citation(1, "https://unique-b.example/path", "b"),
            _citation(2, "https://DUP.example/path?utm=2", "dup again"),
        ],
        sub_questions=["b"],
        coverage={},
    )

    merged, refmap = r._merge_subreport_citations([("qa", report_a), ("qb", report_b)])

    assert [c.n for c in merged] == [1, 2, 3]
    assert len({c.n for c in merged}) == 3
    assert refmap[(0, 1)] == refmap[(1, 2)]
    assert refmap[(0, 2)] == 2
    assert refmap[(1, 1)] == 3
    assert r._norm_url("HTTPS://Dup.Example/path/?utm=3") == "https://dup.example/path"


def test_tg4_strip_footnote_definitions_removes_appendix_but_keeps_body() -> None:
    finalized, _, _ = r._finalize_report_md(
        "# T\n\nBody keeps its reference [^1].",
        [_citation(1, "https://example.com/a")],
        [],
    )

    stripped = r._strip_footnote_definitions(finalized)

    assert stripped == "# T\n\nBody keeps its reference [^1]."
    assert "[^1]:" not in stripped


def test_tg4_rewrite_local_refs_handles_collisions() -> None:
    md = "First local [^1], second local [^2], unchanged [^9]."

    rewritten = r._rewrite_local_refs(md, {1: 3, 2: 1})

    assert rewritten == "First local [^3], second local [^1], unchanged [^9]."


@pytest.mark.asyncio
async def test_tg4_fanout_synthesize_falls_back_and_cite_check_passes() -> None:
    async def _failing_llm(prompt: str) -> str:
        raise RuntimeError("synth down")

    errors: list[str] = []
    report_md, citations = await r._fanout_synthesize(
        "fallback topic",
        "fallback request",
        [
            ("q1?", _sub_report("q1?", 1, url="https://a.example/doc")),
            ("q2?", _sub_report("q2?", 2, url="https://b.example/doc")),
        ],
        _failing_llm,
        errors,
    )

    cc = r.cite_check(report_md, citations)
    assert any("fanout_synth_llm" in e for e in errors)
    assert [c.n for c in citations] == [1, 2]
    assert "[^1]" in report_md
    assert "[^2]" in report_md
    assert cc["ok"] is True


def test_tg4_finalize_report_md_returns_markdown_citations_and_cite_check() -> None:
    errors: list[str] = []

    report_md, citations, cc = r._finalize_report_md(
        "# T\n\nBody [^1].",
        [_citation(1, "https://example.com/a")],
        errors,
    )

    assert isinstance(report_md, str)
    assert [c.n for c in citations] == [1]
    assert cc["ok"] is True
    assert errors == []
    assert "[^1]:" in report_md


def test_tg5_fanout_config_helpers_read_real_config_raw(monkeypatch) -> None:
    import config

    cfg = SimpleNamespace(raw={
        "research": {
            "subagent_fanout": True,
            "fanout_max_subquestions": 9,
            "fanout_min_subquestions": 3,
            "fanout_subrun_mode": "inherit",
        },
        "agent": {
            "concurrency": {
                "global_concurrency": 3,
                "lane_caps": {"research": 1},
            }
        },
    })
    monkeypatch.setattr(config, "config", cfg, raising=False)
    r._RESEARCH_RAW_CACHE = None

    assert r._fanout_enabled() is True
    assert r._fanout_min_subquestions() == 3
    assert r._fanout_max_subquestions() == 9
    assert r._fanout_subrun_mode("deep") == "deep"
    assert r._fanout_concurrency() == 1


def test_tg5_fanout_config_helpers_fall_back_without_raw_stub(monkeypatch) -> None:
    import config

    monkeypatch.setattr(config, "config", object(), raising=False)
    monkeypatch.setattr(config, "load_config", lambda path: SimpleNamespace(raw={}))
    monkeypatch.setattr(config, "resolve_config_path", lambda: None)
    r._RESEARCH_RAW_CACHE = None

    assert r._fanout_enabled() is False
    assert r._fanout_min_subquestions() == 2
    assert r._fanout_max_subquestions() == 6
    assert r._fanout_subrun_mode("standard") == "light"
    assert r._fanout_concurrency() == 2
