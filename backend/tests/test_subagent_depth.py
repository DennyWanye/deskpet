# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""WI-OC-1 — 子代理 spawn 显式 depth 计数 + 上界。

测试矩阵（plan §3.4 WI-OC-1）：
  ① flag ON + depth 达上界 → spawn 拒绝（SpawnDepthExceeded / forbidden）。
  ② depth 未达上界 → 正常放行。
  ③ flag OFF（默认） → 不检查 depth、走 strip（BC，depth 字段不影响行为）。
  ④ 硬上限 HARD_MAX_SPAWN_DEPTH=3 不可超过（即使 max_spawn_depth 配更大）。
"""
from __future__ import annotations

import json

import pytest

from deskpet.agent.task_kinds import (
    HARD_MAX_SPAWN_DEPTH,
    SpawnDepthExceeded,
    check_spawn_depth,
    child_depth_env,
    current_spawn_depth,
    depth_gate_enabled,
    resolve_max_spawn_depth,
    _DEPTH_ENV,
)

# ── 纯函数：flag 读取 ──────────────────────────────────────────────────


def test_flag_default_off_is_bc():  # ③ BC：缺省 → False
    assert depth_gate_enabled(None) is False
    assert depth_gate_enabled({}) is False
    assert depth_gate_enabled({"foo": "bar"}) is False
    # 非 dict 也安全回退 False
    assert depth_gate_enabled("not-a-dict") is False  # type: ignore[arg-type]


def test_flag_on_when_set_true():
    assert depth_gate_enabled({"subagent_explicit_depth": True}) is True
    assert depth_gate_enabled({"subagent_explicit_depth": False}) is False


# ── 纯函数：depth 读取 ─────────────────────────────────────────────────


def test_current_depth_default_zero(monkeypatch):
    monkeypatch.delenv(_DEPTH_ENV, raising=False)
    assert current_spawn_depth() == 0


def test_current_depth_reads_env(monkeypatch):
    monkeypatch.setenv(_DEPTH_ENV, "2")
    assert current_spawn_depth() == 2


def test_current_depth_bad_value_falls_back_zero(monkeypatch):
    monkeypatch.setenv(_DEPTH_ENV, "garbage")
    assert current_spawn_depth() == 0
    monkeypatch.setenv(_DEPTH_ENV, "-5")
    assert current_spawn_depth() == 0  # clamp 到 >=0


def test_child_depth_env_increments(monkeypatch):
    monkeypatch.delenv(_DEPTH_ENV, raising=False)
    assert child_depth_env() == {_DEPTH_ENV: "1"}
    assert child_depth_env(2) == {_DEPTH_ENV: "3"}


# ── 纯函数：上界解析 + 硬上限 ──────────────────────────────────────────


def test_default_max_depth_is_one():
    assert resolve_max_spawn_depth(None) == 1
    assert resolve_max_spawn_depth({}) == 1


def test_max_depth_config_respected():
    assert resolve_max_spawn_depth({"max_spawn_depth": 2}) == 2


def test_hard_cap_three_cannot_be_exceeded():  # ④ 硬上限 3
    assert HARD_MAX_SPAWN_DEPTH == 3
    # 配 5 / 99 都被 clamp 到 3
    assert resolve_max_spawn_depth({"max_spawn_depth": 5}) == 3
    assert resolve_max_spawn_depth({"max_spawn_depth": 99}) == 3


def test_max_depth_below_one_clamped_up():
    assert resolve_max_spawn_depth({"max_spawn_depth": 0}) == 1
    assert resolve_max_spawn_depth({"max_spawn_depth": -3}) == 1


def test_max_depth_bad_value_falls_back_default():
    assert resolve_max_spawn_depth({"max_spawn_depth": "x"}) == 1


# ── check_spawn_depth：核心门控逻辑 ─────────────────────────────────────


def test_gate_off_is_noop_regardless_of_depth():  # ③ flag OFF → 永不抛
    # 即使 depth 很深，OFF 时直接返回（BC：depth 字段不影响行为）
    check_spawn_depth(None, current_depth=99)
    check_spawn_depth({}, current_depth=99)
    check_spawn_depth({"max_spawn_depth": 1}, current_depth=99)  # 无 flag → OFF


def test_gate_on_below_limit_allows():  # ② depth 未达上界 → 放行
    cfg = {"subagent_explicit_depth": True, "max_spawn_depth": 1}
    # 顶层(0) spawn → 子深度 1 ≤ 1 → 放行（无异常）
    check_spawn_depth(cfg, current_depth=0)


def test_gate_on_at_limit_rejects():  # ① flag ON + depth 达上界 → 拒绝
    cfg = {"subagent_explicit_depth": True, "max_spawn_depth": 1}
    # 子代理(depth=1) 再 spawn → 子深度 2 > 1 → 拒绝
    with pytest.raises(SpawnDepthExceeded) as ei:
        check_spawn_depth(cfg, current_depth=1)
    assert ei.value.depth == 2
    assert ei.value.limit == 1


def test_gate_on_deeper_limit_allows_controlled_nesting():
    # flag ON + max=2 → 顶层(0)→子(1) 放行，子(1)→孙(2) 放行，孙(2)→曾孙(3) 拒绝
    cfg = {"subagent_explicit_depth": True, "max_spawn_depth": 2}
    check_spawn_depth(cfg, current_depth=0)  # 子深度 1 ≤ 2
    check_spawn_depth(cfg, current_depth=1)  # 子深度 2 ≤ 2
    with pytest.raises(SpawnDepthExceeded):
        check_spawn_depth(cfg, current_depth=2)  # 子深度 3 > 2


def test_gate_hard_cap_enforced_even_with_big_config():  # ④
    # 配 max=99 但硬上限 3：depth=3 时子深度 4 > clamp(3) → 拒绝
    cfg = {"subagent_explicit_depth": True, "max_spawn_depth": 99}
    check_spawn_depth(cfg, current_depth=2)  # 子深度 3 ≤ 3 → 放行
    with pytest.raises(SpawnDepthExceeded) as ei:
        check_spawn_depth(cfg, current_depth=3)  # 子深度 4 > 3（硬上限）
    assert ei.value.limit == HARD_MAX_SPAWN_DEPTH


def test_gate_reads_env_when_no_explicit_depth(monkeypatch):
    cfg = {"subagent_explicit_depth": True, "max_spawn_depth": 1}
    monkeypatch.setenv(_DEPTH_ENV, "1")  # 模拟子代理环境
    with pytest.raises(SpawnDepthExceeded):
        check_spawn_depth(cfg)  # current_depth 从 env 读 → 1 → 子深度 2 > 1


# ── 集成：spawn_subagents 真 handler 路径 ──────────────────────────────


def _make_spawn_handler(monkeypatch, agent_cfg):
    """构造真 spawn_subagents handler，并 monkeypatch config 读取。"""
    from deskpet.tools.code_tools import agent_parallel_tool, spawn_subagents_tool

    monkeypatch.setattr(
        agent_parallel_tool, "_read_raw_agent_cfg", lambda: agent_cfg
    )

    class _FakeScheduler:
        async def run(self, *, kind, run_id, task_id, parent_sid, coro_factory):
            return await coro_factory()

    class _FakeRegistry:
        def __init__(self):
            self.registered = []

        def register(self, run):
            self.registered.append(run)

        def complete(self, rid, *, summary, stats=None):
            pass

        def fail(self, rid, error):
            pass

    # runner 不真起 LLM —— 直接 monkeypatch native runner factory
    monkeypatch.setattr(
        spawn_subagents_tool,
        "_make_async_native_runner",
        lambda **kw: (lambda sar, tid: _fake_runner(sar, tid)),
    )

    (spawn, _schema), (_await, _aschema) = (
        spawn_subagents_tool.build_spawn_subagents_tools(
            llm_shim=object(),
            parent_tool_registry=object(),
            parent_session_id_resolver=lambda: "sess",
            scheduler=_FakeScheduler(),
            registry=_FakeRegistry(),
        )
    )
    return spawn


async def _fake_runner(sar, tid):
    return f"done:{tid}"


@pytest.mark.asyncio
async def test_spawn_handler_off_allows(monkeypatch):  # ③ BC：OFF → spawn 成功
    spawn = _make_spawn_handler(monkeypatch, {})  # 无 flag → OFF
    out = await spawn({"subagents": [{"prompt": "do x"}]})
    data = json.loads(out)
    assert data["ok"] is True
    assert data["run_ids"]


@pytest.mark.asyncio
async def test_spawn_handler_on_at_limit_rejected(monkeypatch):  # ① 拒绝
    monkeypatch.setenv(_DEPTH_ENV, "1")  # 本代理已在 depth=1
    spawn = _make_spawn_handler(
        monkeypatch, {"subagent_explicit_depth": True, "max_spawn_depth": 1}
    )
    out = await spawn({"subagents": [{"prompt": "do x"}]})
    data = json.loads(out)
    assert data["ok"] is False
    assert data["forbidden"] == "spawn_depth"


@pytest.mark.asyncio
async def test_spawn_handler_on_below_limit_allows(monkeypatch):  # ②
    monkeypatch.delenv(_DEPTH_ENV, raising=False)  # 顶层 depth=0
    spawn = _make_spawn_handler(
        monkeypatch, {"subagent_explicit_depth": True, "max_spawn_depth": 1}
    )
    out = await spawn({"subagents": [{"prompt": "do x"}]})
    data = json.loads(out)
    assert data["ok"] is True  # 子深度 1 ≤ 1 → 放行
