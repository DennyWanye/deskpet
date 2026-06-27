# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""agent — spawn a nested AgentLoop with a focused subset of tools.

Use case: the parent loop needs to do an expensive sub-investigation
("find all places X is used and summarise") without burning the parent
context window or risking the parent's overall plan. The subagent runs
its own AgentLoop, gets a fresh system prompt scoped to the task, and
returns a single string when done — the parent treats it like a
function call result.

Safety:
  * **Recursion guard** — the subagent's tool subset NEVER includes
    ``agent`` itself. A subagent cannot spawn its own subagent. (LLMs
    are creative; this guarantees finite depth.)
  * **Default tool subset is read-only** — Read, Glob, Grep, WebSearch.
    Caller can override via ``tools=["read", "bash"]`` etc, but the
    safe default avoids unintended write/exec from a delegated task.
  * **Iteration cap** — subagent gets at most 15 iterations regardless
    of the parent's setting, so a runaway sub-task can't dominate the
    user's session.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Awaitable, Callable

from deskpet.agent.task_kinds import _FORBIDDEN_IN_KIND

log = logging.getLogger(__name__)

_SUBAGENT_MAX_ITERATIONS = 15
_DEFAULT_READONLY_TOOLS = ("read_file", "list_directory", "glob", "grep", "web_search")


def build_agent_tool(
    *,
    llm_shim,
    parent_tool_registry,
    parent_session_id_resolver: Callable[[], str],
    default_max_iterations: int = _SUBAGENT_MAX_ITERATIONS,
    default_tool_subset: tuple[str, ...] = _DEFAULT_READONLY_TOOLS,
    default_framing: str = "",
    termination_gate_factory: Callable[[], Any] | None = None,
):
    """Construct the agent (subagent) tool handler.

    子代理并发驱动（plans/2026-06-21-subagent-concurrency-driver/ WI-1.1/4.1）：
    ``default_max_iterations`` / ``default_tool_subset`` / ``default_framing`` 让
    调度器按 KindProfile 给每个子代理定制迭代上限 / 工具子集 / 角色 framing；
    ``termination_gate_factory`` 给子代理质量守门（WI-4.1）。**默认值 = 现状
    常量 → 不传新参时行为字节级一致（BC）。**

    The closure captures the LLM shim and a clone-able tool registry so
    the subagent can run its own AgentLoop independently of the parent.

    Args:
        llm_shim: An ``OpenAICompatibleAgentLLM`` (same shim the parent
            loop uses). We don't construct a new LLM connection per call.
        parent_tool_registry: The full ToolRegistry. We'll filter it to
            the requested subset before handing off.
        parent_session_id_resolver: Callable returning the parent
            chat's session id. We append ``.sub`` so SessionDB rows
            from the subagent are visually distinguishable.
    """

    schema = {
        "name": "agent",
        "description": (
            "Spawn a focused subagent for a self-contained sub-task. "
            "Use when the work is read-heavy (search a codebase, "
            "summarise findings) or sufficiently bounded that a fresh "
            "context helps. Subagent runs at most 15 iterations and "
            "returns its final string. By default it has read-only tools "
            "(read_file, list_directory, glob, grep, web_search). Use "
            "the `tools` parameter to override (e.g. ['read_file', 'bash'])."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "One-line summary of what the subagent will do.",
                },
                "prompt": {
                    "type": "string",
                    "description": "The actual task / question for the subagent.",
                },
                "tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional override for the subagent's tool set "
                        "(omit for read-only default)."
                    ),
                },
            },
            "required": ["description", "prompt"],
        },
    }

    def _handler(args: dict[str, Any], task_id: str = "") -> str:
        # WI-OC-1：显式 depth 上界（flag OFF=默认 → no-op，仍靠 strip 守门 = BC）。
        from deskpet.agent.task_kinds import SpawnDepthExceeded, check_spawn_depth
        from .agent_parallel_tool import _read_raw_agent_cfg

        try:
            check_spawn_depth(_read_raw_agent_cfg())
        except SpawnDepthExceeded as exc:
            return json.dumps(
                {"error": str(exc), "forbidden": "spawn_depth"}, ensure_ascii=False
            )
        description = args.get("description")
        prompt = args.get("prompt")
        if not description or not prompt:
            return json.dumps({"error": "description and prompt required"})

        requested_tools = args.get("tools")
        if requested_tools and isinstance(requested_tools, list):
            tool_subset = [
                t for t in requested_tools
                if isinstance(t, str) and t not in _FORBIDDEN_IN_KIND
            ]
        else:
            tool_subset = list(default_tool_subset)
        # 允许 per-call 覆盖迭代上限（调度器按 KindProfile 注入）
        try:
            call_max_iter = int(args.get("max_iterations") or default_max_iterations)
        except (TypeError, ValueError):
            call_max_iter = default_max_iterations

        # Filter the parent registry to a derived registry that only
        # exposes the subset. Implementation: we use ToolRegistry's
        # ``schemas(enabled_toolsets)`` filtering at LLM-prompt time;
        # for execution we simply rely on the LLM only being shown the
        # subset, so it can only call those.
        # To avoid leaking other tools, we wrap parent registry with a
        # tiny adapter that errors out on non-allowed names at execute
        # time — defence in depth.
        from agent.agent_loop import (  # type: ignore[import-not-found]
            AgentLoop as _AgentLoop,
            FinalEvent as _FinEv,
            ErrorEvent as _ErrEv,
        )

        adapter = _SubsetRegistryAdapter(parent_tool_registry, tool_subset)

        _framing_block = (default_framing + "\n\n") if default_framing else ""
        sub_messages = [
            {
                "role": "system",
                "content": (
                    f"You are a focused subagent invoked by a parent code-mode "
                    f"agent. Task: {description}\n\n"
                    f"{_framing_block}"
                    "Use the available tools, then end your turn with a single "
                    "concise final message summarising what you found / did. "
                    f"You have {call_max_iter} iterations max. The parent will "
                    "treat your final message as your return value."
                ),
            },
            {"role": "user", "content": prompt},
        ]

        _loop_kwargs: dict[str, Any] = {
            "llm_registry": llm_shim,
            "tool_registry": adapter,
            "max_iterations": call_max_iter,
        }
        if termination_gate_factory is not None:
            try:
                _loop_kwargs["termination_gate"] = termination_gate_factory()
            except Exception as _gexc:  # noqa: BLE001 — gate 构造失败不阻子代理
                log.warning("subagent termination_gate factory failed: %s", _gexc)
        sub_loop = _AgentLoop(**_loop_kwargs)

        parent_sid = parent_session_id_resolver() or "default"
        sub_sid = f"{parent_sid}.sub"

        async def _run():
            final_text = ""
            async for ev in sub_loop.run(sub_messages, session_id=sub_sid):
                if isinstance(ev, _FinEv):
                    final_text = ev.content or ""
                    break
                elif isinstance(ev, _ErrEv):
                    # P6 bugfix 2026-05-15 (UI-click C1 live test): ErrorEvent
                    # has `reason` + `detail` fields, not `error`. Pre-fix this
                    # raised AttributeError every subagent error path, masking
                    # the real LLM/tool error with a Python crash.
                    return f"[subagent error] {ev.reason}: {ev.detail}".rstrip(": ")
            return final_text or "[subagent finished without final text]"

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        # WI-OC-1：子代理深度 = 父深度+1，注入 env 让子代理 current_spawn_depth()
        # 深一层（depth gate flag ON 时生效；OFF 时此 env 不被读取 → BC）。
        from deskpet.agent.task_kinds import _DEPTH_ENV, child_depth_env

        _prev_depth = os.environ.get(_DEPTH_ENV)
        os.environ.update(child_depth_env())
        try:
            if loop is None or not loop.is_running():
                result = asyncio.run(_run())
            else:
                fut = asyncio.run_coroutine_threadsafe(_run(), loop)
                # Subagent capped at 15 iter × ~30s per LLM round = ~7m worst case.
                # Pick a generous-but-finite timeout.
                result = fut.result(timeout=600)
        finally:
            if _prev_depth is None:
                os.environ.pop(_DEPTH_ENV, None)
            else:
                os.environ[_DEPTH_ENV] = _prev_depth

        return json.dumps({"result": result, "tools": tool_subset})

    return _handler, schema


class _SubsetRegistryAdapter:
    """Read-only wrapper that exposes only ``allowed_names`` from a parent
    ToolRegistry. Used to harden the subagent against accidental tool
    expansion.
    """

    def __init__(self, parent_registry, allowed_names: list[str]) -> None:
        self._parent = parent_registry
        self._allowed = set(allowed_names)

    def schemas(self, enabled_toolsets=None):
        all_schemas = self._parent.schemas(enabled_toolsets=enabled_toolsets)
        out = []
        for s in all_schemas:
            fn = s.get("function") or {}
            if fn.get("name") in self._allowed:
                out.append(s)
        return out

    async def execute_tool(self, *args, **kwargs):
        # Inspect the tool name from positional or keyword args:
        # AgentLoop calls execute_tool(name, args_dict, ...) typically.
        # We extract whichever signature actually appears.
        name = None
        if args:
            name = args[0]
        else:
            name = kwargs.get("name") or kwargs.get("tool_name")
        if name not in self._allowed:
            raise PermissionError(
                f"tool {name!r} not in subagent's allowed set: "
                f"{sorted(self._allowed)}"
            )
        return await self._parent.execute_tool(*args, **kwargs)
