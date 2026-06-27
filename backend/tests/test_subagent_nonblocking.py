# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""TG-3.1/3.2/3.3 — 非阻塞子代理 registry + spawn/await + agent_loop drain。"""
from __future__ import annotations

import asyncio
import inspect
import json

import pytest

from agent.agent_loop import AgentLoop, SubagentCompletionEvent
from deskpet.agent.subagent_registry import SubagentRegistry, SubagentRun
from deskpet.agent.subagent_scheduler import SubagentScheduler


# --- minimal fakes (复用 compaction_wiring 风格) ---------------------------
class _FakeToolRegistry:
    def schemas(self, enabled_toolsets=None):
        return []

    async def execute_tool(self, name, args, task_id):
        return '{"ok": true, "result": "noop"}'


class _FakeLLM:
    def __init__(self):
        self.calls = 0

    async def chat_with_fallback(self, messages, *, tools=None, model=None, **kw):
        self.calls += 1
        from llm.types import ChatResponse

        return ChatResponse(
            content="done", stop_reason="end_turn", tool_calls=[],
            usage={"input_tokens": 100, "output_tokens": 10},
        )


# ===== TG-3.1 SubagentRegistry =====
@pytest.mark.asyncio
async def test_registry_lifecycle():  # 3.1.1
    reg = SubagentRegistry()
    run = SubagentRun(run_id="r1", kind="research", task_id="t1")
    reg.register(run)
    assert reg.get("r1") is run
    reg.set_running("r1")
    assert reg.get("r1").status == "running"
    reg.complete("r1", summary="done")
    assert reg.get("r1").status == "completed"
    assert reg.completion_queue.get_nowait().run_id == "r1"


@pytest.mark.asyncio
async def test_registry_fail_enqueues():
    reg = SubagentRegistry()
    reg.register(SubagentRun("r1", "code", "t1"))
    reg.fail("r1", "boom")
    r = reg.get("r1")
    assert r.status == "failed" and "boom" in r.error
    assert reg.completion_queue.get_nowait().run_id == "r1"


@pytest.mark.asyncio
async def test_cancel_all():  # 3.1.2 ★V5
    reg = SubagentRegistry()

    async def sleeper():
        await asyncio.sleep(10)

    t = asyncio.create_task(sleeper())
    await asyncio.sleep(0)  # 让 task 真正起来
    reg.register(SubagentRun("r1", "general", "t1", status="running", task=t))
    reg.register(SubagentRun("r2", "general", "t2", status="completed"))  # 不取消
    n = reg.cancel_all()
    assert n == 1
    assert reg.get("r1").status == "cancelled"
    await asyncio.sleep(0)
    assert t.cancelled() or t.done()


# ===== TG-3.2 spawn_subagents / await_subagents =====
def _fake_runner_factory(**kw):
    async def runner(sa, tid):
        await asyncio.sleep(0.01)
        return f"res:{tid}"

    return runner


def _build_tools(monkeypatch, reg, sched):
    monkeypatch.setattr(
        "deskpet.tools.code_tools.spawn_subagents_tool._make_async_native_runner",
        _fake_runner_factory,
    )
    from deskpet.tools.code_tools.spawn_subagents_tool import (
        build_spawn_subagents_tools,
    )

    return build_spawn_subagents_tools(
        llm_shim=None,
        parent_tool_registry=None,
        parent_session_id_resolver=lambda: "sid",
        scheduler=sched,
        registry=reg,
    )


@pytest.mark.asyncio
async def test_spawn_returns_immediately_and_await_collects(monkeypatch):  # 3.2.1/3.2.2
    reg = SubagentRegistry()
    sched = SubagentScheduler()
    (spawn_h, _s), (await_h, _a) = _build_tools(monkeypatch, reg, sched)

    out = json.loads(
        await spawn_h(
            {
                "subagents": [
                    {"task_id": "a", "prompt": "x", "kind": "research"},
                    {"task_id": "b", "prompt": "y", "kind": "doc"},
                ]
            },
            "",
        )
    )
    assert out["ok"] and len(out["run_ids"]) == 2
    assert len(reg.list()) == 2  # 立即返回时 run 已登记

    aout = json.loads(await await_h({}, ""))
    assert aout["ok"] and len(aout["results"]) == 2
    assert all(r["status"] == "completed" for r in aout["results"])
    assert all(r["output"].startswith("res:") for r in aout["results"])


@pytest.mark.asyncio
async def test_spawn_rejects_empty(monkeypatch):
    reg = SubagentRegistry()
    sched = SubagentScheduler()
    (spawn_h, _s), _ = _build_tools(monkeypatch, reg, sched)
    out = json.loads(await spawn_h({"subagents": []}, ""))
    assert out["ok"] is False


# ===== TG-3.3 agent_loop drain =====
def test_ctor_accepts_subagent_registry():  # 3.3.2 BC param
    p = inspect.signature(AgentLoop.__init__).parameters
    assert "subagent_registry" in p
    assert p["subagent_registry"].default is None


@pytest.mark.asyncio
async def test_drain_injects_completion():  # 3.3.1
    reg = SubagentRegistry()
    reg.completion_queue.put_nowait(
        SubagentRun(run_id="r1", kind="research", task_id="t1",
                    status="completed", summary="找到了答案")
    )
    loop = AgentLoop(
        llm_registry=_FakeLLM(), tool_registry=_FakeToolRegistry(),
        subagent_registry=reg, max_iterations=2,
    )
    events = []
    async for ev in loop.run([{"role": "user", "content": "hi"}], session_id="sid"):
        events.append(ev)
    assert any(
        isinstance(e, SubagentCompletionEvent) and e.run_id == "r1"
        for e in events
    )


@pytest.mark.asyncio
async def test_drain_registry_none_is_bc():  # 3.3.2 BC
    loop = AgentLoop(
        llm_registry=_FakeLLM(), tool_registry=_FakeToolRegistry(),
        max_iterations=2,
    )
    assert loop._subagent_registry is None
    events = []
    async for ev in loop.run([{"role": "user", "content": "hi"}], session_id="sid"):
        events.append(ev)
    assert not any(isinstance(e, SubagentCompletionEvent) for e in events)
