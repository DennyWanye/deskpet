# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""TG-4.1/4.2 — 子代理质量守门 + per-kind 模型路由（native runner hooks）。"""
from __future__ import annotations

import pytest

from deskpet.tools.code_tools.agent_parallel_tool import _make_async_native_runner


class _Final:
    def __init__(self, content):
        self.content = content


class _Err:
    pass


class _FakeLoop:
    instances: list = []

    def __init__(self, **kw):
        _FakeLoop.instances.append(kw)

    async def run(self, messages, session_id=None):
        yield _Final("ok")


@pytest.fixture
def patched(monkeypatch):
    _FakeLoop.instances = []
    monkeypatch.setattr("agent.agent_loop.AgentLoop", _FakeLoop, raising=False)
    monkeypatch.setattr("agent.agent_loop.FinalEvent", _Final, raising=False)
    monkeypatch.setattr("agent.agent_loop.ErrorEvent", _Err, raising=False)
    return _FakeLoop


def _runner(**kw):
    return _make_async_native_runner(
        llm_shim="PARENT",
        parent_tool_registry=None,
        parent_session_id_resolver=lambda: "sid",
        **kw,
    )


@pytest.mark.asyncio
async def test_model_routing(patched):  # 4.2.1
    m1 = object()
    runner = _runner(shim_resolver=lambda m: {"m1": m1}.get(m))
    await runner(
        {"prompt": "p", "tools": ["read_file"], "_max_iter": 10, "_model": "m1"}, "t1"
    )
    assert patched.instances[-1]["llm_registry"] is m1


@pytest.mark.asyncio
async def test_model_none_uses_parent(patched):  # 4.2.2 ★BC
    runner = _runner(shim_resolver=lambda m: object())
    await runner(
        {"prompt": "p", "tools": ["read_file"], "_max_iter": 10, "_model": None}, "t1"
    )
    assert patched.instances[-1]["llm_registry"] == "PARENT"


@pytest.mark.asyncio
async def test_model_resolver_failure_falls_back(patched):  # 多做：resolver 抛回退
    def boom(_m):
        raise RuntimeError("x")

    runner = _runner(shim_resolver=boom)
    await runner(
        {"prompt": "p", "tools": ["read_file"], "_max_iter": 10, "_model": "m1"}, "t1"
    )
    assert patched.instances[-1]["llm_registry"] == "PARENT"


@pytest.mark.asyncio
async def test_gate_factory_passed(patched):  # 4.1.1
    sentinel = object()
    runner = _runner(termination_gate_factory=lambda: sentinel)
    await runner({"prompt": "p", "tools": ["read_file"], "_max_iter": 10}, "t1")
    assert patched.instances[-1].get("termination_gate") is sentinel


@pytest.mark.asyncio
async def test_no_gate_by_default(patched):  # 4.1.2 BC（用 AgentLoop 自带默认 gate）
    runner = _runner()
    await runner({"prompt": "p", "tools": ["read_file"], "_max_iter": 10}, "t1")
    assert "termination_gate" not in patched.instances[-1]
