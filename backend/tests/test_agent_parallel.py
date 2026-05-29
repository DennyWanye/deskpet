# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-C1 — agent_parallel 工具单元测试 (companion-code-skill-upgrade v1, Stage C).

测试焦点（不调真 LLM）：
  * 参数校验：count < 2 / > 4 / 非 list / 缺 prompt
  * Sprint Contract JSON 注入正确（pure func）
  * recursion guard 剔除 ``agent`` / ``agent_parallel`` 嵌套
  * 真并发：≥2 个 subagent 启动 timestamp 差 < 200ms
  * 错误隔离：单 subagent 异常时其他 result 仍 ok=true，整体 envelope ok=true
  * metrics.jsonl subagent_progress event 真出现（monkeypatch metrics_sink.record）

所有测试用 ``subagent_runner`` 注入 mock，不依赖 LLM / agent_tool 闭包。
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from deskpet.tools.code_tools.agent_parallel_tool import (
    _FORBIDDEN_NESTED_TOOLS,
    _SCHEMA,
    _build_sprint_contract,
    _filter_subagent_tools,
    build_agent_parallel_tool,
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_schema_shape_min_max():
    """Schema declares 2-4 subagents min/max."""
    sub_spec = _SCHEMA["parameters"]["properties"]["subagents"]
    assert sub_spec["minItems"] == 2
    assert sub_spec["maxItems"] == 4
    item_required = sub_spec["items"]["required"]
    assert "task_id" in item_required
    assert "prompt" in item_required


def test_build_sprint_contract_injects_json_block():
    sa = {
        "task_id": "frontend",
        "prompt": "重写 SessionGridView.tsx",
        "input_files": ["src/code-panel/SessionGridView.tsx"],
        "output_files": ["src/code-panel/SessionGridView.tsx"],
        "forbidden_files": ["src/App.tsx"],
        "success_criteria": "npm run build passes",
    }
    out = _build_sprint_contract(sa)
    assert "# Sprint Contract (auto-injected)" in out
    assert "```json" in out
    assert "# Task" in out
    assert "重写 SessionGridView.tsx" in out
    # JSON block must be parseable
    start = out.index("```json\n") + len("```json\n")
    end = out.index("\n```", start)
    contract = json.loads(out[start:end])
    assert contract["task_id"] == "frontend"
    assert contract["input_files"] == ["src/code-panel/SessionGridView.tsx"]
    assert contract["forbidden_files"] == ["src/App.tsx"]
    assert contract["success_criteria"] == "npm run build passes"


def test_build_sprint_contract_handles_missing_optional_fields():
    """Only task_id + prompt are required; everything else gets sensible default."""
    out = _build_sprint_contract({"task_id": "t", "prompt": "do x"})
    start = out.index("```json\n") + len("```json\n")
    end = out.index("\n```", start)
    contract = json.loads(out[start:end])
    assert contract["input_files"] == []
    assert contract["output_files"] == []
    assert contract["forbidden_files"] == []
    assert contract["success_criteria"] == ""


def test_filter_subagent_tools_strips_recursive_names():
    """Recursion guard — agent + agent_parallel always removed."""
    out = _filter_subagent_tools(["read_file", "agent", "grep", "agent_parallel"])
    assert out == ["read_file", "grep"]
    assert _FORBIDDEN_NESTED_TOOLS == {"agent", "agent_parallel"}


def test_filter_subagent_tools_none_passthrough():
    """None preserves agent_tool's default-read-only behaviour."""
    assert _filter_subagent_tools(None) is None
    assert _filter_subagent_tools([]) is None


# ---------------------------------------------------------------------------
# Handler — validation
# ---------------------------------------------------------------------------


def _build_handler(runner=None):
    """Test helper: build the parallel tool with a mock runner."""
    return build_agent_parallel_tool(
        llm_shim=None,
        parent_tool_registry=None,
        parent_session_id_resolver=lambda: "test_sid",
        subagent_runner=runner,
    )[0]


@pytest.mark.asyncio
async def test_validation_rejects_count_below_minimum():
    """count == 1 → ok=false."""
    handler = _build_handler(runner=_make_echo_runner())
    out = json.loads(
        await handler({"subagents": [{"task_id": "a", "prompt": "x"}]}, "")
    )
    assert out["ok"] is False
    assert "2-4" in out["error"]


@pytest.mark.asyncio
async def test_validation_rejects_count_above_maximum():
    """count == 5 → ok=false."""
    handler = _build_handler(runner=_make_echo_runner())
    five = [{"task_id": f"t{i}", "prompt": "x"} for i in range(5)]
    out = json.loads(await handler({"subagents": five}, ""))
    assert out["ok"] is False
    assert "2-4" in out["error"]


@pytest.mark.asyncio
async def test_validation_rejects_non_list_subagents():
    handler = _build_handler(runner=_make_echo_runner())
    out = json.loads(await handler({"subagents": "not a list"}, ""))
    assert out["ok"] is False
    assert "list" in out["error"]


@pytest.mark.asyncio
async def test_validation_rejects_subagent_missing_prompt():
    handler = _build_handler(runner=_make_echo_runner())
    out = json.loads(
        await handler(
            {
                "subagents": [
                    {"task_id": "a", "prompt": "ok"},
                    {"task_id": "b"},  # missing prompt
                ]
            },
            "",
        )
    )
    assert out["ok"] is False
    assert "prompt" in out["error"]


# ---------------------------------------------------------------------------
# Concurrency + aggregation
# ---------------------------------------------------------------------------


def _make_echo_runner():
    """Mock runner: returns 'echo:<task_id>' immediately."""

    async def _runner(sa_for_runner: dict[str, Any], task_id: str) -> str:
        return f"echo:{task_id}"

    return _runner


@pytest.mark.asyncio
async def test_two_subagents_run_concurrently():
    """Real concurrency check — two subagents that each sleep 100ms must
    finish in < 180ms (not 200ms serial). Also asserts start timestamps
    are within 200ms of each other.
    """
    start_times: list[float] = []
    barrier_lock = asyncio.Lock()

    async def _runner(sa_for_runner: dict[str, Any], task_id: str) -> str:
        async with barrier_lock:
            start_times.append(time.time())
        await asyncio.sleep(0.1)
        return f"done:{task_id}"

    handler = _build_handler(runner=_runner)
    t0 = time.time()
    out = json.loads(
        await handler(
            {
                "subagents": [
                    {"task_id": "a", "prompt": "x"},
                    {"task_id": "b", "prompt": "y"},
                ]
            },
            "",
        )
    )
    elapsed = time.time() - t0
    assert out["ok"] is True
    assert out["count"] == 2
    # Serial would be ≥ 200ms; concurrent should be ~100ms + overhead
    assert elapsed < 0.18, f"expected concurrent < 0.18s, got {elapsed:.3f}s"
    # Start timestamps within 200ms of each other
    assert len(start_times) == 2
    assert abs(start_times[1] - start_times[0]) < 0.2


@pytest.mark.asyncio
async def test_aggregated_results_include_both_subagents():
    """Final envelope.results lists every task_id with its output."""
    handler = _build_handler(runner=_make_echo_runner())
    out = json.loads(
        await handler(
            {
                "subagents": [
                    {"task_id": "alpha", "prompt": "x"},
                    {"task_id": "beta", "prompt": "y"},
                    {"task_id": "gamma", "prompt": "z"},
                ]
            },
            "",
        )
    )
    assert out["ok"] is True
    assert out["count"] == 3
    ids = {r["task_id"] for r in out["results"]}
    assert ids == {"alpha", "beta", "gamma"}
    for r in out["results"]:
        assert r["ok"] is True
        assert r["output"] == f"echo:{r['task_id']}"


@pytest.mark.asyncio
async def test_subagent_exception_isolated_to_its_result():
    """One subagent raises — its result has ok=False, others still ok=True,
    overall envelope ok=True (not a hard failure).
    """

    async def _runner(sa_for_runner: dict[str, Any], task_id: str) -> str:
        if task_id == "boom":
            raise RuntimeError("simulated subagent crash")
        return f"ok:{task_id}"

    handler = _build_handler(runner=_runner)
    out = json.loads(
        await handler(
            {
                "subagents": [
                    {"task_id": "good", "prompt": "x"},
                    {"task_id": "boom", "prompt": "y"},
                ]
            },
            "",
        )
    )
    # Overall still OK — agent_parallel itself dispatched fine
    assert out["ok"] is True
    by_id = {r["task_id"]: r for r in out["results"]}
    assert by_id["good"]["ok"] is True
    assert by_id["good"]["output"] == "ok:good"
    assert by_id["boom"]["ok"] is False
    assert "RuntimeError" in by_id["boom"]["error"]
    assert "simulated subagent crash" in by_id["boom"]["error"]


# ---------------------------------------------------------------------------
# Sprint Contract injection at handler level
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_prepends_sprint_contract_to_prompt():
    """Runner sees the contract-augmented prompt, NOT the raw user prompt."""
    seen_prompts: list[str] = []

    async def _runner(sa_for_runner: dict[str, Any], task_id: str) -> str:
        seen_prompts.append(sa_for_runner["prompt"])
        return "ok"

    handler = _build_handler(runner=_runner)
    await handler(
        {
            "subagents": [
                {
                    "task_id": "frontend",
                    "prompt": "build SessionGridView",
                    "input_files": ["src/foo.tsx"],
                    "forbidden_files": ["src/App.tsx"],
                },
                {"task_id": "backend", "prompt": "add /goal route"},
            ]
        },
        "",
    )
    assert len(seen_prompts) == 2
    for p in seen_prompts:
        assert "# Sprint Contract (auto-injected)" in p
        assert "# Task" in p
    # The frontend prompt mentions its forbidden file
    frontend_prompt = next(p for p in seen_prompts if "build SessionGridView" in p)
    assert "src/App.tsx" in frontend_prompt
    assert "src/foo.tsx" in frontend_prompt


@pytest.mark.asyncio
async def test_handler_strips_recursive_tools_before_runner():
    """Runner receives a tool list with agent/agent_parallel removed."""
    seen_tools: list[Any] = []

    async def _runner(sa_for_runner: dict[str, Any], task_id: str) -> str:
        seen_tools.append(sa_for_runner.get("tools"))
        return "ok"

    handler = _build_handler(runner=_runner)
    await handler(
        {
            "subagents": [
                {
                    "task_id": "a",
                    "prompt": "x",
                    "tools": ["read_file", "agent", "agent_parallel", "grep"],
                },
                {
                    "task_id": "b",
                    "prompt": "y",
                    # No tools → None passthrough
                },
            ]
        },
        "",
    )
    assert seen_tools[0] == ["read_file", "grep"]
    assert seen_tools[1] is None


# ---------------------------------------------------------------------------
# Metrics emission
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subagent_progress_metrics_emitted(monkeypatch):
    """Each subagent must emit at least 'starting' + 'completed' events."""
    captured: list[tuple[str, dict]] = []

    def _fake_record(event: str, detail: dict | None = None) -> bool:
        captured.append((event, dict(detail or {})))
        return True

    # Patch the symbol on the module that agent_parallel_tool imports
    import observability.metrics_sink as ms

    monkeypatch.setattr(ms, "record", _fake_record)

    handler = _build_handler(runner=_make_echo_runner())
    out = json.loads(
        await handler(
            {
                "subagents": [
                    {"task_id": "alpha", "prompt": "x"},
                    {"task_id": "beta", "prompt": "y"},
                ]
            },
            "",
        )
    )
    assert out["ok"] is True

    progress_events = [
        (ev, det) for ev, det in captured if ev == "subagent_progress"
    ]
    # Each subagent: ≥ 1 starting + ≥ 1 completed
    starting = [d for ev, d in progress_events if d.get("status") == "starting"]
    completed = [d for ev, d in progress_events if d.get("status") == "completed"]
    assert {d["task_id"] for d in starting} == {"alpha", "beta"}
    assert {d["task_id"] for d in completed} == {"alpha", "beta"}


@pytest.mark.asyncio
async def test_subagent_progress_failed_event_on_exception(monkeypatch):
    captured: list[tuple[str, dict]] = []

    def _fake_record(event: str, detail: dict | None = None) -> bool:
        captured.append((event, dict(detail or {})))
        return True

    import observability.metrics_sink as ms

    monkeypatch.setattr(ms, "record", _fake_record)

    async def _runner(sa_for_runner: dict[str, Any], task_id: str) -> str:
        if task_id == "boom":
            raise ValueError("kaboom")
        return "ok"

    handler = build_agent_parallel_tool(
        llm_shim=None,
        parent_tool_registry=None,
        parent_session_id_resolver=lambda: "sid",
        subagent_runner=_runner,
    )[0]
    await handler(
        {
            "subagents": [
                {"task_id": "good", "prompt": "x"},
                {"task_id": "boom", "prompt": "y"},
            ]
        },
        "",
    )

    failed = [
        d for ev, d in captured
        if ev == "subagent_progress" and d.get("status") == "failed"
    ]
    assert len(failed) == 1
    assert failed[0]["task_id"] == "boom"


@pytest.mark.asyncio
async def test_metrics_emission_failure_does_not_break_dispatch(monkeypatch):
    """If observability.metrics_sink.record blows up, subagents still run."""

    def _explode(event: str, detail: dict | None = None) -> bool:
        raise RuntimeError("metrics sink broken")

    import observability.metrics_sink as ms

    monkeypatch.setattr(ms, "record", _explode)

    handler = _build_handler(runner=_make_echo_runner())
    out = json.loads(
        await handler(
            {
                "subagents": [
                    {"task_id": "alpha", "prompt": "x"},
                    {"task_id": "beta", "prompt": "y"},
                ]
            },
            "",
        )
    )
    assert out["ok"] is True
    assert out["count"] == 2
    assert all(r["ok"] for r in out["results"])
