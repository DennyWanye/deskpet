# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""TG-1.2/1.3 — agent_parallel 事务分型 + 调度器路由 + 背压。"""
from __future__ import annotations

import asyncio
import json

import pytest

from deskpet.agent.subagent_scheduler import SubagentScheduler
from deskpet.tools.code_tools.agent_parallel_tool import (
    _SCHEMA,
    _make_async_native_runner,
    build_agent_parallel_tool,
)


def _record_runner(calls):
    async def runner(sa_for_runner, task_id):
        calls.append(sa_for_runner)
        return f"done:{task_id}"

    return runner


def _build(runner, scheduler=None):
    h, _ = build_agent_parallel_tool(
        llm_shim=None,
        parent_tool_registry=None,
        parent_session_id_resolver=lambda: "sid",
        subagent_runner=runner,
        scheduler=scheduler,
    )
    return h


def test_schema_kind_enum_and_max():  # 1.2.1
    sub = _SCHEMA["parameters"]["properties"]["subagents"]
    assert sub["maxItems"] == 8
    kp = sub["items"]["properties"]["kind"]
    assert set(kp["enum"]) == {"general", "research", "code", "fileops", "doc", "web"}


@pytest.mark.asyncio
async def test_kind_routes_tools_and_scheduler():  # 1.3.1
    calls = []
    sched = SubagentScheduler(global_concurrency=4)
    out = json.loads(
        await _build(_record_runner(calls), scheduler=sched)(
            {
                "subagents": [
                    {"task_id": "a", "prompt": "x", "kind": "research"},
                    {"task_id": "b", "prompt": "y", "kind": "doc"},
                ]
            },
            "",
        )
    )
    assert out["ok"]
    by = {c["task_id"]: c for c in calls}
    assert "deepresearch" not in by["a"]["tools"]  # research kind 工具集
    assert "ppt_create" in by["b"]["tools"]  # doc kind 工具集
    assert by["a"]["_kind"] == "research" and by["b"]["_kind"] == "doc"
    assert by["a"]["_max_iter"] == 12 and by["b"]["_max_iter"] == 15
    kinds = {r["task_id"]: r["kind"] for r in out["results"]}
    assert kinds == {"a": "research", "b": "doc"}


@pytest.mark.asyncio
async def test_scheduler_none_is_bc():  # 1.3.2 ★BC
    calls = []
    out = json.loads(
        await _build(_record_runner(calls))(  # scheduler=None
            {
                "subagents": [
                    {"task_id": "a", "prompt": "x"},
                    {"task_id": "b", "prompt": "y"},
                ]
            },
            "",
        )
    )
    assert out["ok"] and out["count"] == 2
    assert calls[0]["_kind"] == "general"  # 无 kind → general 默认


@pytest.mark.asyncio
async def test_explicit_tools_strip_forbidden():  # 1.3.3
    calls = []
    sched = SubagentScheduler()
    await _build(_record_runner(calls), scheduler=sched)(
        {
            "subagents": [
                {
                    "task_id": "a",
                    "prompt": "x",
                    "kind": "code",
                    "tools": ["read_file", "agent", "agent_parallel"],
                },
                {"task_id": "b", "prompt": "y", "kind": "code"},
            ]
        },
        "",
    )
    by = {c["task_id"]: c for c in calls}
    assert "read_file" in by["a"]["tools"]
    assert "agent" not in by["a"]["tools"]
    assert "agent_parallel" not in by["a"]["tools"]


@pytest.mark.asyncio
async def test_unknown_kind_falls_back_general():  # 1.3.4
    calls = []
    sched = SubagentScheduler()
    out = json.loads(
        await _build(_record_runner(calls), scheduler=sched)(
            {
                "subagents": [
                    {"task_id": "a", "prompt": "x", "kind": "xyz"},
                    {"task_id": "b", "prompt": "y"},
                ]
            },
            "",
        )
    )
    assert out["ok"]
    assert {c["task_id"]: c["_kind"] for c in calls}["a"] == "general"


@pytest.mark.asyncio
async def test_backpressure_six_global_cap_four():  # 1.3.5 ★V3
    peak = {"n": 0, "max": 0}
    events: list[str] = []

    async def slow(sa, tid):
        peak["n"] += 1
        peak["max"] = max(peak["max"], peak["n"])
        await asyncio.sleep(0.02)
        peak["n"] -= 1
        return "ok"

    sched = SubagentScheduler(
        global_concurrency=4,
        lane_caps={"general": 8},  # lane 不限，专测 global cap
        progress_sink=lambda p: events.append(p["status"]),
    )
    six = [{"task_id": f"t{i}", "prompt": "x", "kind": "general"} for i in range(6)]
    out = json.loads(await _build(slow, scheduler=sched)({"subagents": six}, ""))
    assert out["ok"] and out["count"] == 6  # 全完成不丢
    assert peak["max"] <= 4  # global cap 背压
    assert "queued" in events  # 有排队（背压触发）


@pytest.mark.asyncio
async def test_per_subagent_error_isolation():  # 1.3.6
    async def runner(sa, tid):
        if tid == "bad":
            raise RuntimeError("boom")
        return "ok"

    out = json.loads(
        await _build(runner)(
            {
                "subagents": [
                    {"task_id": "good", "prompt": "x"},
                    {"task_id": "bad", "prompt": "y"},
                ]
            },
            "",
        )
    )
    assert out["ok"] is True  # envelope 整体仍 ok
    by = {r["task_id"]: r for r in out["results"]}
    assert by["good"]["ok"] is True
    assert by["bad"]["ok"] is False and "boom" in by["bad"]["error"]


def test_native_runner_constructs():  # 1.3.7 native runner 可构造（深度行为留真机）
    r = _make_async_native_runner(
        llm_shim=None,
        parent_tool_registry=None,
        parent_session_id_resolver=lambda: "sid",
    )
    assert asyncio.iscoroutinefunction(r)
