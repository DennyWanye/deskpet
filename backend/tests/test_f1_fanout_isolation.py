# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""F1 (memory-stage2-followup) — assembler fanout per-component 隔离测试。

旧实现：ComponentRegistry.fanout 用一个整体 ``asyncio.wait_for`` 包住
所有组件的 gather —— 任何一个组件慢（e.g. MemoryComponent 向量召回碰
BGE-M3 冷加载）就让整批超时，所有组件（连 trivial 的 time/persona）
一起变空 slice，且 log 只打 ``pending=[全部]`` 无法定位真凶。

修复：每个组件独立计时 + 独立软超时。慢的单独降级，快的照常返回真
内容，并把 ``duration_ms`` / ``status`` 记进 slice.meta，使 round-3 那种
``fanout_timed_out pending=[workspace,...]`` 能精确到组件。

本文件自包含（不依赖其它 test 的 fixtures），钉死新行为。
"""
from __future__ import annotations

import asyncio

import pytest

from deskpet.agent.assembler.bundle import AssemblyPolicy, Slice
from deskpet.agent.assembler.components.base import ComponentContext
from deskpet.agent.assembler.registry import ComponentRegistry


class _Stub:
    """最小组件 stub：可配延时 + 返回文本。"""

    def __init__(self, name: str, delay: float = 0.0, text: str = "") -> None:
        self.name = name
        self._delay = delay
        self._text = text

    async def provide(self, ctx: ComponentContext) -> Slice:
        if self._delay:
            await asyncio.sleep(self._delay)
        return Slice(
            component_name=self.name,
            text_content=self._text,
            tokens=max(0, len(self._text) // 4),
            priority=50,
            bucket="dynamic",
        )


def _ctx(policy: AssemblyPolicy) -> ComponentContext:
    return ComponentContext(
        task_type=policy.task_type,
        policy=policy,
        user_message="hi",
    )


@pytest.mark.asyncio
async def test_f1_slow_component_does_not_starve_fast_one():
    """核心 F1 修复：一个慢组件超时不应让快组件也变空。"""
    reg = ComponentRegistry()
    reg.register(_Stub("memory", delay=0.0, text="REAL"))
    reg.register(_Stub("slow", delay=2.0, text="SLOW"))
    policy = AssemblyPolicy(task_type="chat", must=["memory", "slow"])
    slices = await reg.fanout(_ctx(policy), timeout_ms=200)
    by_name = {s.component_name: s for s in slices}
    # 快组件保留真内容（旧实现这里会被整体超时拖成空）
    assert by_name["memory"].text_content == "REAL"
    assert by_name["memory"].meta.get("status") == "ok"
    # 慢组件单独降级为 timeout
    assert by_name["slow"].meta.get("error") == "timeout"
    assert by_name["slow"].meta.get("status") == "timeout"


@pytest.mark.asyncio
async def test_f1_each_slice_has_duration_ms():
    """每个组件 slice 都带 duration_ms（诊断哪个组件慢）。"""
    reg = ComponentRegistry()
    reg.register(_Stub("memory", delay=0.0))
    reg.register(_Stub("persona", delay=0.0))
    policy = AssemblyPolicy(task_type="chat", must=["memory", "persona"])
    slices = await reg.fanout(_ctx(policy), timeout_ms=500)
    for s in slices:
        assert "duration_ms" in s.meta
        assert isinstance(s.meta["duration_ms"], (int, float))
        assert s.meta.get("status") == "ok"


@pytest.mark.asyncio
async def test_f1_no_timeout_path_still_stamps_status():
    """timeout_ms=None（无预算）时也照常返回真内容 + status=ok。"""
    reg = ComponentRegistry()
    reg.register(_Stub("memory", delay=0.0, text="X"))
    policy = AssemblyPolicy(task_type="chat", must=["memory"])
    slices = await reg.fanout(_ctx(policy), timeout_ms=None)
    assert slices[0].text_content == "X"
    assert slices[0].meta.get("status") == "ok"


@pytest.mark.asyncio
async def test_f1_exception_component_isolated_with_status():
    """异常组件单独降级 status=error，不影响同批其它组件。"""
    reg = ComponentRegistry()
    reg.register(_Stub("memory", delay=0.0, text="OK"))
    boom = _Stub("boom", delay=0.0)

    async def _boom(ctx):
        raise RuntimeError("kaboom")

    boom.provide = _boom  # type: ignore[assignment]
    reg.register(boom)
    policy = AssemblyPolicy(task_type="chat", must=["memory", "boom"])
    slices = await reg.fanout(_ctx(policy), timeout_ms=500)
    by_name = {s.component_name: s for s in slices}
    assert by_name["memory"].text_content == "OK"
    assert by_name["boom"].meta.get("status") == "error"
    assert "kaboom" in by_name["boom"].meta.get("error", "")


@pytest.mark.asyncio
async def test_f1_all_fast_components_all_ok():
    """全部快组件：都应 status=ok 且保留内容（无误降级）。"""
    reg = ComponentRegistry()
    for n in ("memory", "persona", "time", "tool", "workspace"):
        reg.register(_Stub(n, delay=0.0, text=n.upper()))
    policy = AssemblyPolicy(
        task_type="chat",
        must=["memory"],
        prefer=["persona", "time", "tool", "workspace"],
    )
    slices = await reg.fanout(_ctx(policy), timeout_ms=1500)
    by_name = {s.component_name: s for s in slices}
    assert len(by_name) == 5
    for n in ("memory", "persona", "time", "tool", "workspace"):
        assert by_name[n].meta.get("status") == "ok"
        assert by_name[n].text_content == n.upper()
