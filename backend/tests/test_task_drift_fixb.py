from __future__ import annotations

import inspect
import time

import pytest

from agent.agent_loop import _inject_loop_user_request, _tool_declares_user_request
from deskpet.tools import research_tools as r
from llm.types import ToolCall


ORIGINAL_REQUEST = "Please deeply research Rust Tokio architecture and competitors"


def test_plan_prompt_includes_authoritative_user_request() -> None:
    prompt = r._PLAN_PROMPT.format(
        topic="CATL drift value",
        user_request=ORIGINAL_REQUEST,
    )

    assert "ORIGINAL USER REQUEST" in prompt
    assert "authoritative; derive ALL sub-questions from THIS" in prompt
    assert ORIGINAL_REQUEST in prompt
    assert "CANDIDATE TOPIC FROM TOOL ARGS" in prompt
    assert "untrusted" in prompt
    assert "CATL drift value" in prompt


@pytest.mark.asyncio
async def test_deepresearch_user_request_none_falls_back_to_topic_without_keyerror(monkeypatch) -> None:
    monkeypatch.setattr(r, "_query_expansion_enabled", lambda: False)
    monkeypatch.setattr(r, "_direct_sources_enabled", lambda: False)
    seen_prompts: list[str] = []

    async def fake_llm(prompt: str) -> str:
        seen_prompts.append(prompt)
        return '["What is Rust Tokio?"]'

    async def fake_search(query: str, *, max_results: int = 4):
        return []

    report = await r.deepresearch(
        "Rust Tokio",
        llm_call=fake_llm,
        search=fake_search,
        user_request=None,
    )

    assert report.topic == "Rust Tokio"
    assert seen_prompts
    assert "ORIGINAL USER REQUEST" in seen_prompts[0]
    assert "Rust Tokio" in seen_prompts[0]


@pytest.mark.asyncio
async def test_deepresearch_uses_original_request_as_canonical_subject(monkeypatch) -> None:
    drift_topic = "宁德时代2024年报"
    request_topic = "深度调研 Rust Tokio 异步运行时"
    calls: dict[str, list[str]] = {
        "expand": [],
        "keywords": [],
        "velocity": [],
        "gap": [],
        "semantic": [],
        "rerank": [],
        "synth_prompts": [],
        "search_queries": [],
    }

    original_keywords = r._topic_keywords

    monkeypatch.setattr(r, "_query_expansion_enabled", lambda: True)
    monkeypatch.setattr(r, "_direct_sources_enabled", lambda: False)
    monkeypatch.setattr(r, "_site_directed_enabled", lambda: False)
    monkeypatch.setattr(r, "_rerank_mode", lambda: "llm")
    monkeypatch.setattr(r, "_RERANK_LLM_CALL", lambda prompt: "[]")

    async def fake_expand(llm_call, topic, sub_questions, errors, *, max_extra=4):
        calls["expand"].append(topic)
        return ["Rust Tokio work stealing scheduler"]

    def fake_keywords(text: str, *, max_keywords: int = 8):
        calls["keywords"].append(text)
        return original_keywords(text, max_keywords=max_keywords)

    def fake_velocity(topic: str):
        calls["velocity"].append(topic)
        return "medium"

    async def fake_gap(llm_call, topic, sub_questions, passages, errors, *, max_followups=3):
        calls["gap"].append(topic)
        return ["Rust Tokio runtime benchmark evidence"]

    async def fake_semantic(topic: str, passage_texts: list[str]):
        calls["semantic"].append(topic)
        return [0.8 for _ in passage_texts]

    async def fake_rerank(topic, passages, rerank_call, errors, *, velocity):
        calls["rerank"].append(topic)
        return False

    monkeypatch.setattr(r, "_expand_queries", fake_expand)
    monkeypatch.setattr(r, "_topic_keywords", fake_keywords)
    monkeypatch.setattr(r.research_scoring, "infer_topic_velocity", fake_velocity)
    monkeypatch.setattr(r, "_gap_followup_queries", fake_gap)
    monkeypatch.setattr(r, "_SEMANTIC_SCORER", fake_semantic)
    monkeypatch.setattr(r, "_llm_rerank", fake_rerank)

    async def fake_llm(prompt: str) -> str:
        if "Break it into 3-6 focused sub-questions" in prompt:
            assert "ORIGINAL USER REQUEST" in prompt
            assert "authoritative; derive ALL sub-questions from THIS" in prompt
            assert request_topic in prompt
            assert "CANDIDATE TOPIC FROM TOOL ARGS" in prompt
            assert drift_topic in prompt
            return '["Rust Tokio scheduler model", "Rust Tokio IO driver"]'
        if "You are writing a research briefing" in prompt:
            calls["synth_prompts"].append(prompt)
            assert f"# {request_topic}" in prompt
            assert f"# {drift_topic}" not in prompt
            return (
                f"# {request_topic}\n\n"
                "## TL;DR\nTokio summary [^1].\n\n"
                "## Background\nTokio evidence [^1]."
            )
        return "[]"

    async def fake_search(query: str, *, max_results: int = 4):
        calls["search_queries"].append(query)
        slug = str(len(calls["search_queries"]))
        return [{"url": f"https://tokio.rs/source-{slug}", "title": query}]

    async def fake_extract(url: str):
        return {
            "ok": True,
            "url": url,
            "title": "Tokio runtime source",
            "text": (
                "Rust Tokio async runtime scheduler IO driver work stealing "
                "reactor executor tasks futures " * 8
            ),
            "date": "2026-01-01",
            "fetched_at": time.time(),
        }

    report = await r.deepresearch(
        drift_topic,
        llm_call=fake_llm,
        search=fake_search,
        extract=fake_extract,
        user_request=request_topic,
        max_sub_questions=2,
        max_urls_per_query=1,
        max_total_passages=3,
        min_passage_chars=20,
        max_rounds=2,
    )

    assert report.topic == request_topic
    assert report.report_md.startswith(f"# {request_topic}")
    assert drift_topic not in report.report_md
    assert calls["expand"] == [request_topic]
    assert request_topic in calls["keywords"]
    assert drift_topic not in calls["keywords"]
    assert calls["velocity"] == [request_topic]
    assert calls["gap"] == [request_topic]
    assert calls["semantic"] == [request_topic]
    assert calls["rerank"] == [request_topic]
    assert calls["synth_prompts"]

    source = inspect.getsource(r.deepresearch)
    assert "request_topic = _ur" in source
    assert "search_specs.append((eq, request_topic))" in source


@pytest.mark.asyncio
async def test_deepresearch_legacy_none_user_request_keeps_topic_as_subject(monkeypatch) -> None:
    topic = "Rust Tokio"
    calls: dict[str, list[str]] = {
        "expand": [],
        "velocity": [],
        "semantic": [],
        "rerank": [],
    }

    monkeypatch.setattr(r, "_query_expansion_enabled", lambda: True)
    monkeypatch.setattr(r, "_direct_sources_enabled", lambda: False)
    monkeypatch.setattr(r, "_site_directed_enabled", lambda: False)
    monkeypatch.setattr(r, "_rerank_mode", lambda: "llm")
    monkeypatch.setattr(r, "_RERANK_LLM_CALL", lambda prompt: "[]")

    async def fake_expand(llm_call, seen_topic, sub_questions, errors, *, max_extra=4):
        calls["expand"].append(seen_topic)
        return ["Tokio runtime"]

    def fake_velocity(seen_topic: str):
        calls["velocity"].append(seen_topic)
        return "medium"

    async def fake_semantic(seen_topic: str, passage_texts: list[str]):
        calls["semantic"].append(seen_topic)
        return [0.8 for _ in passage_texts]

    async def fake_rerank(seen_topic, passages, rerank_call, errors, *, velocity):
        calls["rerank"].append(seen_topic)
        return False

    monkeypatch.setattr(r, "_expand_queries", fake_expand)
    monkeypatch.setattr(r.research_scoring, "infer_topic_velocity", fake_velocity)
    monkeypatch.setattr(r, "_SEMANTIC_SCORER", fake_semantic)
    monkeypatch.setattr(r, "_llm_rerank", fake_rerank)

    async def fake_llm(prompt: str) -> str:
        if "Break it into 3-6 focused sub-questions" in prompt:
            return '["Rust Tokio scheduler"]'
        if "You are writing a research briefing" in prompt:
            return f"# {topic}\n\n## TL;DR\nTokio summary [^1]."
        return "[]"

    async def fake_search(query: str, *, max_results: int = 4):
        return [{"url": "https://tokio.rs/legacy", "title": query}]

    async def fake_extract(url: str):
        return {
            "ok": True,
            "title": "Tokio runtime source",
            "text": "Rust Tokio runtime scheduler executor futures " * 8,
            "date": "2026-01-01",
            "fetched_at": time.time(),
        }

    report = await r.deepresearch(
        topic,
        llm_call=fake_llm,
        search=fake_search,
        extract=fake_extract,
        user_request=None,
        max_sub_questions=1,
        max_urls_per_query=1,
        max_total_passages=1,
        min_passage_chars=20,
    )

    assert report.topic == topic
    assert calls["expand"] == [topic]
    assert calls["velocity"] == [topic]
    assert calls["semantic"] == [topic]
    assert calls["rerank"] == [topic]


@pytest.mark.asyncio
async def test_deepresearch_handler_saves_report_under_returned_topic(monkeypatch, tmp_path) -> None:
    drift_topic = "宁德时代2024年报"
    request_topic = "深度调研 Rust Tokio 异步运行时"
    saved_topics: list[str] = []
    indexed_topics: list[str] = []

    async def fake_resolve_llm():
        async def _llm(prompt: str) -> str:
            return "[]"
        return _llm

    async def fake_deepresearch(topic: str, **kwargs):
        return r.ResearchReport(
            topic=request_topic,
            summary="summary",
            report_md=f"# {request_topic}\n\nTokio evidence [^1].",
            citations=[
                r.Citation(
                    n=1,
                    url="https://tokio.rs",
                    title="Tokio",
                    snippet="Rust Tokio",
                    fetched_at=time.time(),
                )
            ],
            sub_questions=[request_topic],
            coverage={"n_sources": 1, "n_domains": 1, "cite_check_ok": True},
            errors=[],
        )

    def fake_save_report(topic: str, report):
        saved_topics.append(topic)
        return tmp_path / "tokio.md"

    async def fake_update_index(path, topic: str, report):
        indexed_topics.append(topic)

    monkeypatch.setattr(r, "_resolve_default_llm_call", fake_resolve_llm)
    monkeypatch.setattr(r, "deepresearch", fake_deepresearch)
    monkeypatch.setattr(r, "_save_report", fake_save_report)
    monkeypatch.setattr(r, "_update_deepresearch_index", fake_update_index)
    monkeypatch.setattr(r, "get_subagent_scheduler", lambda: None)

    out = await r._handle_deepresearch(
        {"topic": drift_topic, "user_request": request_topic},
        task_id="t1",
    )

    assert request_topic in out
    assert saved_topics == [request_topic]
    assert indexed_topics == [request_topic]


def test_tool_declares_user_request_detects_schema_property() -> None:
    schemas = [
        {
            "name": "deepresearch",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "user_request": {"type": "string"},
                },
            },
        },
        {
            "name": "web_search",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        },
    ]

    assert _tool_declares_user_request("deepresearch", schemas) is True
    assert _tool_declares_user_request("web_search", schemas) is False
    assert _tool_declares_user_request("missing", schemas) is False


def test_dispatch_user_request_injection_unconditionally_overwrites_llm_value() -> None:
    schemas = [
        {
            "name": "deepresearch",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "user_request": {"type": "string"},
                },
            },
        }
    ]
    tc = ToolCall(
        id="call_1",
        name="deepresearch",
        arguments={"topic": "drift value", "user_request": "llm filled wrong"},
    )

    _inject_loop_user_request(
        tc,
        schemas,
        loop_user_request=ORIGINAL_REQUEST,
    )

    assert tc.arguments["user_request"] == ORIGINAL_REQUEST
