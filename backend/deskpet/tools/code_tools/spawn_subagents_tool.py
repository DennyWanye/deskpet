# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""spawn_subagents / await_subagents — 非阻塞子代理 + yield 等待（WI-3.2）。

plan: plans/2026-06-21-subagent-concurrency-driver/ WI-3.2

偷师 OpenClaw 的 sessions_spawn + sessions_yield（模式 4：spawn 立即返回、
单独 await 工具结束回合等结果）。

* ``spawn_subagents`` — 每子任务 ``asyncio.create_task(scheduler.run(...))`` →
  **先** ``registry.register``（持 Task 强引用，gap4）→ 立即返回 ``run_ids``。
* ``await_subagents`` — ``asyncio.wait`` 指定/全部活 run → 收集 results。
* 子代理完成后入 ``registry.completion_queue``，由 agent_loop 在回合边界自动
  drain 注入父上下文（即使不主动 await 也会「冒泡」）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Callable, Optional

from .agent_parallel_tool import (
    _build_sprint_contract,
    _filter_subagent_tools,
    _make_async_native_runner,
)

log = logging.getLogger(__name__)

_KIND_ENUM = ["general", "research", "code", "fileops", "doc", "web"]
_MAX = 8

_SPAWN_SCHEMA: dict[str, Any] = {
    "name": "spawn_subagents",
    "description": (
        "【非阻塞】并发派多个子代理处理【多种事务】，**立即返回 run_ids（不等结果）**。"
        "子代理后台跑完后会在你下一个空闲回合自动把结果冒泡过来；也可主动调 "
        "await_subagents 收集。适用于：后台调研/边聊边干/不想阻塞当前对话。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "subagents": {
                "type": "array",
                "minItems": 1,
                "maxItems": _MAX,
                "items": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "prompt": {"type": "string"},
                        "kind": {"type": "string", "enum": _KIND_ENUM},
                        "tools": {"type": "array", "items": {"type": "string"}},
                        "input_files": {"type": "array", "items": {"type": "string"}},
                        "output_files": {"type": "array", "items": {"type": "string"}},
                        "forbidden_files": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "success_criteria": {"type": "string"},
                    },
                    "required": ["prompt"],
                },
            },
        },
        "required": ["subagents"],
    },
}

_AWAIT_SCHEMA: dict[str, Any] = {
    "name": "await_subagents",
    "description": (
        "等待之前 spawn_subagents 派出的子代理完成并收集结果（结束当前回合等待）。"
        "run_ids 省略 = 等全部活跃子代理。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "run_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要等的 run_id 列表；省略 = 全部活跃子代理。",
            },
        },
        "required": [],
    },
}


def build_spawn_subagents_tools(
    *,
    llm_shim: Any,
    parent_tool_registry: Any,
    parent_session_id_resolver: Callable[[], str],
    scheduler: Any,
    registry: Any,
    kind_overrides: Optional[dict[str, Any]] = None,
    termination_gate_factory: Optional[Callable[[], Any]] = None,
    shim_resolver: Optional[Callable[[str], Any]] = None,
):
    """构造 ``spawn_subagents`` + ``await_subagents`` 两个工具。

    Returns:
        ``((spawn_handler, spawn_schema), (await_handler, await_schema))``
    """
    from deskpet.agent.task_kinds import (
        SpawnDepthExceeded,
        check_spawn_depth,
        resolve_kind,
    )
    from deskpet.agent.subagent_registry import SubagentRun
    from .agent_parallel_tool import _read_raw_agent_cfg

    runner = _make_async_native_runner(
        llm_shim=llm_shim,
        parent_tool_registry=parent_tool_registry,
        parent_session_id_resolver=parent_session_id_resolver,
        termination_gate_factory=termination_gate_factory,
        shim_resolver=shim_resolver,
    )

    async def _spawn(args: dict[str, Any], task_id: str = "") -> str:
        subs = args.get("subagents")
        if not isinstance(subs, list) or not subs:
            return json.dumps(
                {"ok": False, "error": "subagents must be a non-empty list"},
                ensure_ascii=False,
            )
        # WI-OC-1：显式 depth 上界（flag OFF=默认 → no-op，仍靠 strip 守门 = BC）。
        # flag ON 且本代理深度已达上界 → 拒绝整批 spawn（与剥 spawn 工具同风格拒绝）。
        try:
            check_spawn_depth(_read_raw_agent_cfg())
        except SpawnDepthExceeded as exc:
            return json.dumps(
                {"ok": False, "error": str(exc), "forbidden": "spawn_depth"},
                ensure_ascii=False,
            )
        parent_sid = parent_session_id_resolver() or "default"
        run_ids: list[str] = []
        for idx, sa in enumerate(subs[:_MAX]):
            if not isinstance(sa, dict) or not sa.get("prompt"):
                continue
            sa_task_id = sa.get("task_id") or f"sub_{idx}"
            prof = resolve_kind(sa.get("kind"), overrides=kind_overrides)
            run_id = f"{parent_sid}.spawn-{sa_task_id}-{uuid.uuid4().hex[:4]}"
            full_prompt = _build_sprint_contract(sa)
            req_tools = _filter_subagent_tools(sa.get("tools"))
            tools = req_tools if req_tools is not None else list(prof.tools)
            sa_for_runner = {
                **sa,
                "prompt": full_prompt,
                "tools": tools,
                "_kind": prof.kind,
                "_max_iter": prof.max_iterations,
                "_framing": prof.framing,
                "_model": prof.model,  # P4 WI-4.2: per-kind 模型路由（None=父模型 BC）
            }

            async def _wrapped(
                rid=run_id, k=prof.kind, tid=sa_task_id, sar=sa_for_runner
            ):
                try:
                    out = await scheduler.run(
                        kind=k, run_id=rid, task_id=tid, parent_sid=parent_sid,
                        coro_factory=lambda: runner(sar, tid),
                    )
                    registry.complete(rid, summary=str(out))
                    return out
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — 隔离单子代理
                    registry.fail(rid, f"{type(exc).__name__}: {exc}")
                    return None

            t = asyncio.create_task(_wrapped())
            # gap4: 注册（持 Task 强引用）必须在返回前同步完成
            registry.register(
                SubagentRun(
                    run_id=run_id, kind=prof.kind, task_id=sa_task_id,
                    status="queued", task=t,
                )
            )
            run_ids.append(run_id)

        if not run_ids:
            return json.dumps(
                {"ok": False, "error": "no valid subagent (need prompt)"},
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "ok": True,
                "run_ids": run_ids,
                "note": (
                    "子代理已后台并发运行；完成后会在下一回合自动冒泡结果，"
                    "或调 await_subagents 主动等待收集。"
                ),
            },
            ensure_ascii=False,
        )

    async def _await(args: dict[str, Any], task_id: str = "") -> str:
        run_ids = args.get("run_ids")
        if run_ids and isinstance(run_ids, list):
            runs = [registry.get(r) for r in run_ids]
            runs = [r for r in runs if r is not None]
        else:
            runs = registry.list(active_only=True)
        tasks = [r.task for r in runs if r.task is not None]
        if tasks:
            await asyncio.wait(tasks)
        results = [r.to_result() for r in runs]
        return json.dumps({"ok": True, "results": results}, ensure_ascii=False)

    return (_spawn, _SPAWN_SCHEMA), (_await, _AWAIT_SCHEMA)


__all__ = ["build_spawn_subagents_tools", "_SPAWN_SCHEMA", "_AWAIT_SCHEMA"]
