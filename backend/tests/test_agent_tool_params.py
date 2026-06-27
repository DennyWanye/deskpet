# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""TG-1.1 — build_agent_tool 的 iter/tool-subset/framing/gate 参数（WI-1.1/4.1）。"""
from __future__ import annotations

import json

import pytest

from deskpet.tools.code_tools.agent_tool import (
    _DEFAULT_READONLY_TOOLS,
    _SUBAGENT_MAX_ITERATIONS,
    build_agent_tool,
)


class _Final:
    def __init__(self, content):
        self.content = content


class _Err:
    pass


class _FakeLoop:
    instances: list = []
    last_messages = None

    def __init__(self, **kw):
        _FakeLoop.instances.append(kw)

    async def run(self, messages, session_id=None):
        _FakeLoop.last_messages = messages
        yield _Final("subagent-result")


@pytest.fixture
def patched(monkeypatch):
    _FakeLoop.instances = []
    _FakeLoop.last_messages = None
    monkeypatch.setattr("agent.agent_loop.AgentLoop", _FakeLoop, raising=False)
    monkeypatch.setattr("agent.agent_loop.FinalEvent", _Final, raising=False)
    monkeypatch.setattr("agent.agent_loop.ErrorEvent", _Err, raising=False)
    return _FakeLoop


def _build(**kw):
    h, _ = build_agent_tool(
        llm_shim=None,
        parent_tool_registry=None,
        parent_session_id_resolver=lambda: "sid",
        **kw,
    )
    return h


def test_default_call_is_bc(patched):  # 1.1.1 ★BC
    out = json.loads(_build()({"description": "d", "prompt": "p"}, ""))
    assert out["result"] == "subagent-result"
    kw = patched.instances[-1]
    assert kw["max_iterations"] == _SUBAGENT_MAX_ITERATIONS  # 15 默认
    assert kw["tool_registry"]._allowed == set(_DEFAULT_READONLY_TOOLS)


def test_custom_max_iterations(patched):  # 1.1.2
    _build(default_max_iterations=20)({"description": "d", "prompt": "p"}, "")
    assert patched.instances[-1]["max_iterations"] == 20


def test_custom_tool_subset(patched):  # 1.1.3
    _build(default_tool_subset=("read_file", "run_shell"))(
        {"description": "d", "prompt": "p"}, ""
    )
    assert patched.instances[-1]["tool_registry"]._allowed == {"read_file", "run_shell"}


def test_framing_in_system(patched):  # 1.1.4
    _build(default_framing="FRAMING_MARKER")({"description": "d", "prompt": "p"}, "")
    assert "FRAMING_MARKER" in _FakeLoop.last_messages[0]["content"]


def test_args_max_iterations_override(patched):  # 多做：per-call 覆盖
    _build()({"description": "d", "prompt": "p", "max_iterations": 7}, "")
    assert patched.instances[-1]["max_iterations"] == 7


def test_gate_factory_passed(patched):  # 4.1.1
    sentinel = object()
    _build(termination_gate_factory=lambda: sentinel)(
        {"description": "d", "prompt": "p"}, ""
    )
    assert patched.instances[-1].get("termination_gate") is sentinel


def test_no_gate_by_default(patched):  # 4.1.2 BC
    _build()({"description": "d", "prompt": "p"}, "")
    assert "termination_gate" not in patched.instances[-1]
