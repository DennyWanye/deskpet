# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""TG-2.1/2.2/2.3 — spawn_team 暴露为 LLM 工具 + kind 注入 + 注册。"""
from __future__ import annotations

import json

import pytest

from deskpet.agent.team.spawn_team import _TeamSubsetRegistry

# 注意：team/__init__ 把 spawn_team 函数绑到了 team.spawn_team 名字（遮蔽模块），
# pytest 字符串 resolver 也会被带偏 → 用 importlib 直接拿真模块对象再 setattr。
import importlib as _importlib

_spawn_mod = _importlib.import_module("deskpet.agent.team.spawn_team")
from deskpet.tools.code_tools.spawn_team_tool import _SCHEMA, build_spawn_team_tool


def _build(team_store=None):
    h, _ = build_spawn_team_tool(
        llm_shim=None,
        parent_tool_registry=None,
        parent_session_id_resolver=lambda: "sid",
        team_store=team_store or object(),
    )
    return h


def test_schema_shape():  # 2.1.1
    assert _SCHEMA["name"] == "spawn_team"
    assert _SCHEMA["parameters"]["required"] == ["task_descriptions"]
    props = _SCHEMA["parameters"]["properties"]
    for k in ("task_descriptions", "num_teammates", "kind", "timeout_seconds"):
        assert k in props


@pytest.mark.asyncio
async def test_handler_passes_kind_injection(monkeypatch):  # 2.1.2 / 2.2.1
    captured: dict = {}

    async def fake_spawn_team(**kw):
        captured.update(kw)
        return {"ok": True, "results": []}

    monkeypatch.setattr(_spawn_mod, "spawn_team", fake_spawn_team)
    out = json.loads(
        await _build()(
            {"task_descriptions": ["t1", "t2"], "num_teammates": 4, "kind": "code"},
            "",
        )
    )
    assert out["ok"] and out["kind"] == "code"
    assert captured["team_id"].startswith("team-")
    assert captured["task_descriptions"] == ["t1", "t2"]
    assert captured["num_teammates"] == 4
    assert "write_file" in captured["teammate_tool_subset"]  # code kind 工具
    assert captured["teammate_max_iterations"] == 20  # code kind iter
    assert captured["teammate_runner"] is None  # F7：走参数注入不传自定义 runner


@pytest.mark.asyncio
async def test_handler_rejects_empty():  # 边角
    out = json.loads(await _build()({"task_descriptions": []}, ""))
    assert out["ok"] is False


@pytest.mark.asyncio
async def test_handler_clamps_num_teammates(monkeypatch):  # 边角
    captured: dict = {}

    async def fake(**kw):
        captured.update(kw)
        return {"ok": True}

    monkeypatch.setattr(_spawn_mod, "spawn_team", fake)
    await _build()({"task_descriptions": ["x"], "num_teammates": 99}, "")
    assert captured["num_teammates"] == 8  # clamp 到 _MAX_TEAMMATES


def test_team_subset_registry_allowed_tools():  # 2.2.2
    reg = _TeamSubsetRegistry(
        parent_registry=None,
        team_tools=[],
        allowed_tools=("read_file", "write_file", "ppt_create"),
    )
    assert {"read_file", "write_file", "ppt_create"} <= reg._parent_allowed


def test_team_subset_registry_default_bc():  # 2.2.3 ★BC
    reg = _TeamSubsetRegistry(parent_registry=None, team_tools=[])  # allowed_tools=None
    assert reg._parent_allowed == frozenset(
        {"read_file", "list_directory", "glob", "grep", "web_search"}
    )


class _FakeReg:
    def __init__(self):
        self.names: list[str] = []

    def register(self, **kw):
        self.names.append(kw["name"])


def test_registration_registers_spawn_team():  # 2.3.1
    from deskpet.tools.code_tools.registration import register_code_tools

    reg = _FakeReg()
    register_code_tools(
        reg,
        spawn_team_handler=lambda a, task_id="": "{}",
        spawn_team_schema={"name": "spawn_team"},
    )
    assert "spawn_team" in reg.names


def test_registration_skips_when_none():  # 2.3.2 ★BC
    from deskpet.tools.code_tools.registration import register_code_tools

    reg = _FakeReg()
    register_code_tools(reg)
    assert "spawn_team" not in reg.names
