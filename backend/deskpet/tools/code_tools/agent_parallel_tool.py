# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-C1 — ``agent_parallel`` 多子代理并发工具（companion-code-skill-upgrade v1, Stage C）.

Hub-and-spoke 并发派 2–4 个 ephemeral subagents 处理独立子任务，每个
subagent 复用现有 :mod:`deskpet.tools.code_tools.agent_tool` 的
``build_agent_tool`` 工厂（同样的 15 iter cap + recursion guard +
SubsetRegistryAdapter 隔离），通过 ``asyncio.gather`` 真并发执行。

设计要点
--------
* **复用 agent_tool — 不重写**：parallel tool 内部为每个 subagent 构造
  一个 ``build_agent_tool`` 闭包并调用 — agent_tool.py 已经做了所有
  recursion guard / iter cap / SubsetRegistryAdapter / ErrorEvent 处理。
* **递归 guard**：parallel 自身也加 recursion guard — subagent 的 tool
  subset 永远剔除 ``agent`` 和 ``agent_parallel``，禁止嵌套。
* **Sprint Contract 注入**：每个 subagent 的 prompt 被 prepend 一段
  JSON contract（task_id / input_files / output_files / forbidden_files /
  success_criteria），让 LLM 看见明确边界。
* **错误隔离**：单个 subagent 异常被本地 try/except 捕获并写到该 result
  的 ``ok=false``，不影响其他 subagent；整体 envelope 仍 ``ok=true``
  （除非参数校验失败）。
* **进度事件**：每个 subagent start/complete/failed 触发
  ``subagent_progress`` metrics event（best-effort，emit 失败不阻 dispatch）。

工厂模式
--------
``build_agent_parallel_tool(llm_shim, parent_tool_registry,
parent_session_id_resolver)`` → ``(handler, schema)``，与
``build_agent_tool`` 同形参，由 ``main.py`` 启动期拼接闭包。

测试
----
模块同时导出 ``_SCHEMA`` 和 ``_build_sprint_contract`` 给单元测试，并
允许测试通过 ``subagent_runner`` 注入 mock subagent handler 跳过真 LLM。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger(__name__)


_MIN_SUBAGENTS = 2
_MAX_SUBAGENTS = 4
# Recursion guard — 这两个名字永远不会被传给 subagent
_FORBIDDEN_NESTED_TOOLS = frozenset({"agent", "agent_parallel"})


_SCHEMA: dict[str, Any] = {
    "name": "agent_parallel",
    "description": (
        "并发派 2-4 个独立子代理处理可并行子任务（hub-and-spoke 模式）。"
        "每个子代理有独立 prompt + tool subset + 15 iter cap，结果聚合返回。"
        "适用于：多模块改动、多语言翻译、独立调研任务等。"
        "不允许嵌套（subagent 内部 agent/agent_parallel 会被剔除）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "subagents": {
                "type": "array",
                "minItems": _MIN_SUBAGENTS,
                "maxItems": _MAX_SUBAGENTS,
                "description": (
                    f"List of {_MIN_SUBAGENTS}-{_MAX_SUBAGENTS} subagent task "
                    "specs to run concurrently."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "Short identifier for this subagent task.",
                        },
                        "prompt": {
                            "type": "string",
                            "description": "Task / question for this subagent.",
                        },
                        "tools": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional tool subset (defaults to read-only "
                                "if omitted; agent/agent_parallel always stripped)."
                            ),
                        },
                        "input_files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Files this subagent may read.",
                        },
                        "output_files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Files this subagent will write.",
                        },
                        "forbidden_files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Files this subagent must NOT touch.",
                        },
                        "success_criteria": {
                            "type": "string",
                            "description": "How to judge success.",
                        },
                    },
                    "required": ["task_id", "prompt"],
                },
            },
        },
        "required": ["subagents"],
    },
}


def _build_sprint_contract(sa: dict[str, Any]) -> str:
    """Prepend Sprint Contract JSON block to the subagent prompt.

    Pure function — exported for unit tests.
    """
    contract = {
        "task_id": sa.get("task_id", ""),
        "input_files": list(sa.get("input_files") or []),
        "output_files": list(sa.get("output_files") or []),
        "forbidden_files": list(sa.get("forbidden_files") or []),
        "success_criteria": sa.get("success_criteria", "") or "",
    }
    contract_json = json.dumps(contract, ensure_ascii=False, indent=2)
    user_prompt = sa.get("prompt", "") or ""
    return (
        "# Sprint Contract (auto-injected)\n"
        f"```json\n{contract_json}\n```\n\n"
        "# Task\n"
        f"{user_prompt}"
    )


def _emit_progress(task_id: str, status: str) -> None:
    """Best-effort emit ``subagent_progress`` metrics event.

    Never raises — metrics_sink may be unavailable in tests or boot-early
    paths. We swallow any exception so subagent dispatch is never broken
    by observability.
    """
    try:
        from observability.metrics_sink import record  # type: ignore[import-not-found]

        record("subagent_progress", {"task_id": task_id, "status": status})
    except Exception as exc:  # noqa: BLE001 — metrics MUST NOT break dispatch
        log.debug("agent_parallel emit progress failed: %s", exc)


def _filter_subagent_tools(tools: Optional[list[str]]) -> Optional[list[str]]:
    """Strip recursion-forbidden tool names from a requested subset.

    Returns ``None`` if caller didn't pass ``tools`` (preserves agent_tool's
    "default read-only" behaviour). Returns a list with ``agent`` /
    ``agent_parallel`` removed otherwise.
    """
    if not tools or not isinstance(tools, list):
        return None
    return [t for t in tools if isinstance(t, str) and t not in _FORBIDDEN_NESTED_TOOLS]


# Type alias for a subagent runner — accepts the (sa_spec, full_prompt) and
# returns the final string. Default implementation delegates to agent_tool;
# tests inject a mock runner.
SubagentRunner = Callable[[dict[str, Any], str], Awaitable[str]]


def build_agent_parallel_tool(
    *,
    llm_shim: Any,
    parent_tool_registry: Any,
    parent_session_id_resolver: Callable[[], str],
    subagent_runner: Optional[SubagentRunner] = None,
):
    """Construct the ``agent_parallel`` tool handler.

    Args:
        llm_shim: Shared LLM shim (passed through to each subagent's
            inner ``build_agent_tool`` closure).
        parent_tool_registry: Parent ToolRegistry — subagents get
            filtered views via ``SubsetRegistryAdapter``.
        parent_session_id_resolver: Returns parent's session id; each
            subagent appends ``.par-<task_id>`` for traceability.
        subagent_runner: **Test seam** — override the per-subagent runner
            (e.g. inject a mock instead of really spawning AgentLoop).
            None → use ``_default_subagent_runner`` which delegates to
            :func:`build_agent_tool`.
    """
    runner: SubagentRunner = subagent_runner or _make_default_runner(
        llm_shim=llm_shim,
        parent_tool_registry=parent_tool_registry,
        parent_session_id_resolver=parent_session_id_resolver,
    )

    async def _handle(args: dict[str, Any], task_id: str = "") -> str:
        subagents = args.get("subagents")
        if not isinstance(subagents, list):
            return json.dumps(
                {"ok": False, "error": "subagents must be a list"},
                ensure_ascii=False,
            )
        if not _MIN_SUBAGENTS <= len(subagents) <= _MAX_SUBAGENTS:
            return json.dumps(
                {
                    "ok": False,
                    "error": (
                        f"subagents count must be "
                        f"{_MIN_SUBAGENTS}-{_MAX_SUBAGENTS}, "
                        f"got {len(subagents)}"
                    ),
                },
                ensure_ascii=False,
            )

        # Per-subagent normalisation + early validation
        normalised: list[dict[str, Any]] = []
        for idx, sa in enumerate(subagents):
            if not isinstance(sa, dict):
                return json.dumps(
                    {"ok": False, "error": f"subagent[{idx}] must be an object"},
                    ensure_ascii=False,
                )
            sa_task_id = sa.get("task_id") or f"subagent_{idx}"
            sa_prompt = sa.get("prompt")
            if not sa_prompt or not isinstance(sa_prompt, str):
                return json.dumps(
                    {
                        "ok": False,
                        "error": (
                            f"subagent[{idx}] ({sa_task_id}) missing required "
                            "string 'prompt'"
                        ),
                    },
                    ensure_ascii=False,
                )
            normalised.append({**sa, "task_id": sa_task_id})

        start_ts = time.time()

        async def _run_one(sa: dict[str, Any]) -> dict[str, Any]:
            sa_task_id = sa["task_id"]
            full_prompt = _build_sprint_contract(sa)
            # Strip recursion-forbidden tools before handoff
            sa_for_runner = {
                **sa,
                "prompt": full_prompt,
                "tools": _filter_subagent_tools(sa.get("tools")),
            }
            _emit_progress(sa_task_id, "starting")
            try:
                output = await runner(sa_for_runner, sa_task_id)
                _emit_progress(sa_task_id, "completed")
                return {
                    "task_id": sa_task_id,
                    "ok": True,
                    "output": output,
                }
            except Exception as exc:  # noqa: BLE001 — isolate per-subagent
                _emit_progress(sa_task_id, "failed")
                log.warning(
                    "agent_parallel subagent %r failed: %s: %s",
                    sa_task_id, type(exc).__name__, exc,
                )
                return {
                    "task_id": sa_task_id,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }

        # Truly concurrent — gather without return_exceptions because
        # _run_one already catches per-subagent. ``return_exceptions=False``
        # would propagate bugs in our own glue (good signal).
        results = await asyncio.gather(*[_run_one(sa) for sa in normalised])

        elapsed_ms = int((time.time() - start_ts) * 1000)
        return json.dumps(
            {
                "ok": True,
                "elapsed_ms": elapsed_ms,
                "count": len(results),
                "results": results,
            },
            ensure_ascii=False,
        )

    return _handle, _SCHEMA


def _make_default_runner(
    *,
    llm_shim: Any,
    parent_tool_registry: Any,
    parent_session_id_resolver: Callable[[], str],
) -> SubagentRunner:
    """Build the default per-subagent runner that delegates to agent_tool.

    For each subagent we construct a fresh ``build_agent_tool`` closure
    (so each gets its own 15-iter loop) and call its sync handler in a
    thread executor so we don't block the event loop.
    """
    # Lazy import — avoid circular import (agent_tool imports nothing
    # from here, but be defensive).
    from .agent_tool import build_agent_tool

    async def _runner(sa_for_runner: dict[str, Any], sa_task_id: str) -> str:
        # Each parallel subagent gets a distinct session id suffix so its
        # SessionDB rows / metrics are visually separable from siblings.
        parent_sid = parent_session_id_resolver() or "default"

        def _child_sid_resolver() -> str:
            return f"{parent_sid}.par-{sa_task_id}"

        inner_handler, _inner_schema = build_agent_tool(
            llm_shim=llm_shim,
            parent_tool_registry=parent_tool_registry,
            parent_session_id_resolver=_child_sid_resolver,
        )

        # agent_tool's handler is sync (it manages its own asyncio loop
        # internally via run_coroutine_threadsafe). To avoid blocking the
        # parent event loop with N concurrent sync subagents we offload
        # each to the default thread executor.
        loop = asyncio.get_running_loop()

        def _invoke() -> str:
            # agent_tool expects 'description' + 'prompt'
            description = (
                f"agent_parallel child task {sa_task_id}"
            )
            sub_tools = sa_for_runner.get("tools")
            inner_args: dict[str, Any] = {
                "description": description,
                "prompt": sa_for_runner["prompt"],
            }
            if sub_tools is not None:
                inner_args["tools"] = sub_tools
            return inner_handler(inner_args, sa_task_id)

        raw = await loop.run_in_executor(None, _invoke)
        # agent_tool returns JSON: {"result": "...", "tools": [...]}
        # Parse and surface ``result`` so callers see the actual subagent
        # output; if parsing fails, return raw string verbatim.
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and "result" in parsed:
                return str(parsed["result"])
        except (ValueError, TypeError):
            pass
        return raw

    return _runner


__all__ = [
    "_SCHEMA",
    "_build_sprint_contract",
    "_emit_progress",
    "_filter_subagent_tools",
    "_FORBIDDEN_NESTED_TOOLS",
    "build_agent_parallel_tool",
    "SubagentRunner",
]
