# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""spawn_team — LLM-callable 工具，暴露多 teammate 共享任务池（WI-2.1）。

plan: plans/2026-06-21-subagent-concurrency-driver/ WI-2.1

把已写好但未暴露的 :func:`deskpet.agent.team.spawn_team.spawn_team` 封装成一个
LLM 可调用工具（仿 ``build_agent_parallel_tool`` 工厂模式）。

职责分工
--------
* ``spawn_team``   = **同构**共享任务池：N 个 teammate 反复 claim→work→update
  直到池空。适用于「一批同类任务」（翻译 12 个文件 / 批量同种改造）。
* ``agent_parallel`` = **异构**独立任务：每个子任务各自 kind / prompt / 工具集。

team-level kind（WI-2.2）：整队是一个 kind，``teammate_tool_subset`` /
``teammate_max_iterations`` 按 KindProfile 注入到每个 teammate 的 AgentLoop。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

_MIN_TEAMMATES = 1
_MAX_TEAMMATES = 8
_KIND_ENUM = ["general", "research", "code", "fileops", "doc", "web"]

_SCHEMA: dict[str, Any] = {
    "name": "spawn_team",
    "description": (
        "派 N 个同构 teammate 子代理从共享任务池里 claim→work→update，直到池空。"
        "适用于：一批【同类】任务（翻译 12 个文件、批量同种改造）。"
        "若是【多种不同事务】并发（调研+做PPT+查资料）请改用 agent_parallel。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_descriptions": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": "初始任务池，每条一个待办（teammate 会逐个 claim）。",
            },
            "num_teammates": {
                "type": "integer",
                "description": "并发 teammate 数，1-8，默认 3。",
            },
            "kind": {
                "type": "string",
                "enum": _KIND_ENUM,
                "description": (
                    "全队事务类型（决定 teammate 工具集/迭代上限）。"
                    "code=批量改代码、doc=批量出文档、research=批量调研、"
                    "general=通用只读（默认）。"
                ),
            },
            "timeout_seconds": {
                "type": "number",
                "description": "全队墙钟上限（秒），默认 300。",
            },
        },
        "required": ["task_descriptions"],
    },
}


def build_spawn_team_tool(
    *,
    llm_shim: Any,
    parent_tool_registry: Any,
    parent_session_id_resolver: Callable[[], str],
    team_store: Any,
    task_graph_store: Any = None,
    kind_overrides: Optional[dict[str, Any]] = None,
    goal_text_resolver: Optional[Callable[[], Optional[str]]] = None,
    goal_id_resolver: Optional[Callable[[], Optional[str]]] = None,
):
    """构造 ``spawn_team`` 工具 handler。

    Args 同 ``build_agent_parallel_tool`` 风格 + ``team_store``（必给，lifespan
    构造的 :class:`TeamStore`）+ 可选 ``task_graph_store`` / goal resolver。
    """
    # lazy import 防循环
    from deskpet.agent.task_kinds import resolve_kind

    def _safe(resolver: Optional[Callable[[], Optional[str]]]) -> Optional[str]:
        if resolver is None:
            return None
        try:
            return resolver()
        except Exception as exc:  # noqa: BLE001 — resolver 抛不应炸工具
            log.debug("spawn_team goal resolver failed: %s", exc)
            return None

    async def _handle(args: dict[str, Any], task_id: str = "") -> str:
        from deskpet.agent.team.spawn_team import spawn_team

        descs = args.get("task_descriptions")
        if not isinstance(descs, list) or not descs:
            return json.dumps(
                {"ok": False, "error": "task_descriptions must be a non-empty list"},
                ensure_ascii=False,
            )
        try:
            num = int(args.get("num_teammates", 3))
        except (TypeError, ValueError):
            num = 3
        num = max(_MIN_TEAMMATES, min(_MAX_TEAMMATES, num))
        try:
            timeout = float(args.get("timeout_seconds", 300.0))
        except (TypeError, ValueError):
            timeout = 300.0

        prof = resolve_kind(args.get("kind"), overrides=kind_overrides)
        team_id = f"team-{int(time.time())}-{uuid.uuid4().hex[:6]}"

        res = await spawn_team(
            team_id=team_id,
            task_descriptions=[str(d) for d in descs],
            num_teammates=num,
            store=team_store,
            parent_tool_registry=parent_tool_registry,
            llm_shim=llm_shim,
            parent_session_id=parent_session_id_resolver() or "default",
            timeout_seconds=timeout,
            parent_goal_text=_safe(goal_text_resolver),
            parent_goal_id=_safe(goal_id_resolver),
            task_graph_store=task_graph_store,
            teammate_tool_subset=tuple(prof.tools),
            teammate_max_iterations=prof.max_iterations,
            teammate_runner=None,  # 走 _make_default_runner 消费 kind 注入参数
        )
        if isinstance(res, dict):
            res.setdefault("kind", prof.kind)
        return json.dumps(res, ensure_ascii=False)

    return _handle, _SCHEMA


__all__ = ["build_spawn_team_tool", "_SCHEMA"]
