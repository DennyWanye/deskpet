# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""子代理并发驱动 — 裸进程验收冒烟（plan 03-TDD 准入硬条件）。

不依赖 Tauri / 真 LLM / 真模型：纯 import + 构造全部工具 + 调度背压 + kind
路由 + flag-OFF BC，端到端验证接线能拼起来。输出 DECISION: SHIP / NO-SHIP。

用法： cd backend && python -m scripts.acceptance.subagent_driver_smoke
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


_fails: list[str] = []


def _check(name: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(name)


class _FakeReg:
    def __init__(self):
        self.names: list[str] = []

    def register(self, **kw):
        self.names.append(kw["name"])


async def main() -> int:
    print("=== 子代理并发驱动验收冒烟 ===")

    # 1. 事务分型
    from deskpet.agent.task_kinds import resolve_kind, known_kinds

    print("[1] 事务分型 task_kinds")
    _check("6 内置 kind", len(known_kinds()) >= 6, str(known_kinds()))
    _check("research 工具集剥除 deepresearch", "deepresearch" not in resolve_kind("research").tools)
    _check("未知 kind→general", resolve_kind("xyz").kind == "general")
    _check(
        "递归守门(剔 spawn 类)",
        all(
            "agent" not in resolve_kind(k).tools
            and "spawn_team" not in resolve_kind(k).tools
            for k in known_kinds()
        ),
    )

    # 2. 调度器背压（6 任务 global cap=4 → 峰值 ≤4）
    from deskpet.agent.subagent_scheduler import SubagentScheduler

    print("[2] 有界调度 + 背压")
    peak = {"n": 0, "max": 0}
    events: list[str] = []
    sched = SubagentScheduler(
        global_concurrency=4, lane_caps={"general": 8},
        progress_sink=lambda p: events.append(p["status"]),
    )

    async def _job():
        peak["n"] += 1
        peak["max"] = max(peak["max"], peak["n"])
        await asyncio.sleep(0.02)
        peak["n"] -= 1
        return "ok"

    res = await asyncio.gather(*[
        sched.run(kind="general", run_id=f"r{i}", task_id=f"t{i}",
                  parent_sid="p", coro_factory=_job)
        for i in range(6)
    ])
    _check("6 任务全完成", res == ["ok"] * 6)
    _check("global cap=4 背压", peak["max"] <= 4, f"峰值={peak['max']}")
    _check("有排队进度(queued)", "queued" in events)

    # 3. 构造全部工具 + 注册（接线集成）
    print("[3] 工具构造 + 注册")
    from deskpet.tools.code_tools.agent_parallel_tool import build_agent_parallel_tool
    from deskpet.tools.code_tools.spawn_team_tool import build_spawn_team_tool
    from deskpet.tools.code_tools.spawn_subagents_tool import (
        build_spawn_subagents_tools,
    )
    from deskpet.tools.code_tools.registration import register_code_tools
    from deskpet.agent.subagent_registry import SubagentRegistry

    _p_handler, _p_schema = build_agent_parallel_tool(
        llm_shim=None, parent_tool_registry=None,
        parent_session_id_resolver=lambda: "sid",
        scheduler=sched, shim_resolver=lambda m: object(),
    )
    _check("agent_parallel 构造", _p_schema["name"] == "agent_parallel")
    _check("agent_parallel maxItems=8", _p_schema["parameters"]["properties"]
           ["subagents"]["maxItems"] == 8)

    _st_handler, _st_schema = build_spawn_team_tool(
        llm_shim=None, parent_tool_registry=None,
        parent_session_id_resolver=lambda: "sid", team_store=object(),
    )
    _check("spawn_team 构造", _st_schema["name"] == "spawn_team")

    _reg = SubagentRegistry()
    (_sp_h, _sp_s), (_aw_h, _aw_s) = build_spawn_subagents_tools(
        llm_shim=None, parent_tool_registry=None,
        parent_session_id_resolver=lambda: "sid",
        scheduler=sched, registry=_reg,
    )
    _check("spawn_subagents 构造", _sp_s["name"] == "spawn_subagents")
    _check("await_subagents 构造", _aw_s["name"] == "await_subagents")

    fake = _FakeReg()
    register_code_tools(
        fake,
        agent_parallel_handler=_p_handler, agent_parallel_schema=_p_schema,
        spawn_team_handler=_st_handler, spawn_team_schema=_st_schema,
        spawn_subagents_handler=_sp_h, spawn_subagents_schema=_sp_s,
        await_subagents_handler=_aw_h, await_subagents_schema=_aw_s,
    )
    for t in ("agent_parallel", "spawn_team", "spawn_subagents", "await_subagents"):
        _check(f"注册 {t}", t in fake.names)

    # 4. flag-OFF BC：scheduler=None → 扁平 gather 路径仍工作
    print("[4] flag-OFF BC（scheduler=None 扁平路径）")

    async def _echo(sa, tid):
        return f"echo:{tid}"

    h_bc, _ = build_agent_parallel_tool(
        llm_shim=None, parent_tool_registry=None,
        parent_session_id_resolver=lambda: "sid",
        subagent_runner=_echo,  # scheduler=None
    )
    out = json.loads(await h_bc({"subagents": [
        {"task_id": "a", "prompt": "x"}, {"task_id": "b", "prompt": "y"},
    ]}, ""))
    _check("BC 扁平路径 ok", out["ok"] and out["count"] == 2)

    # 5. 注册工具不依赖 spawn 类 handler 时不注册（BC）
    fake2 = _FakeReg()
    register_code_tools(fake2)
    _check("无 handler→不注册 spawn_team", "spawn_team" not in fake2.names)
    _check("无 handler→不注册 spawn_subagents", "spawn_subagents" not in fake2.names)

    print()
    if _fails:
        print(f"DECISION: NO-SHIP — {len(_fails)} 项失败: {_fails}")
        return 1
    print("DECISION: SHIP — 全部接线冒烟通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
