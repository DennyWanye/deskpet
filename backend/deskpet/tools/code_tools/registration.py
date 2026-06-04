# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Register the 5 Code-mode tools into a ToolRegistry.

Called once at backend startup (after the 7 OS tools have been
registered). Permission categories pick safe defaults:

  * glob          → read_file (default-allow)
  * grep          → read_file (default-allow)
  * todo_write    → read_file (it writes only to SessionDB, not user fs)
  * web_search    → network (default-allow when the URL doesn't match
                    any deny pattern — same policy as the existing
                    ``web_fetch`` tool)
  * agent         → read_file (subagent itself uses no resources; its
                    inner tools each go through the gate)

Code mode being "ON" is a runtime concept; we always register the
schemas, but the chat handler can choose to filter them out per-turn
by passing an ``enabled_toolsets`` allow-list to ``registry.schemas()``.
"""
from __future__ import annotations

from typing import Any

from .glob_tool import glob_tool
from .grep_tool import grep_tool
from .web_search_tool import web_search
from .fetch_tool_result_tool import (
    fetch_tool_result_handler,
    FETCH_TOOL_RESULT_SCHEMA,
)


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


def register_code_tools(
    registry,
    *,
    todo_write_handler=None,
    todo_write_schema=None,
    agent_handler=None,
    agent_schema=None,
    agent_parallel_handler=None,
    agent_parallel_schema=None,
) -> None:
    """Register glob/grep/web_search statically + todo_write/agent
    using caller-built closures (because they need session-db / llm-shim
    bindings that only exist at startup time).

    WI-T4.1 v3 (MR-T-11-6): main.py 设计上分两阶段调用本函数 —
    第一次（main.py:343）注册最小集；第二次（main.py:1294）注册含
    todo_write/agent 闭包的全集。两次同名 register 必须有一方 opt-in
    replace_allowed 否则抛 ToolNameConflictError → 整个 v2 init 失败。
    """
    registry.register(
        name="glob",
        toolset="code",
        schema=_schema(
            "glob",
            "Find files by glob pattern under the project root. "
            "Returns paths sorted by mtime (newest first).",
            {
                "pattern": {"type": "string", "description": "e.g. **/*.py"},
                "path": {
                    "type": "string",
                    "description": "Optional override of search root.",
                },
            },
            ["pattern"],
        ),
        handler=glob_tool,
        permission_category="read_file",
        replace_allowed=True,
    )

    registry.register(
        name="grep",
        toolset="code",
        schema=_schema(
            "grep",
            "Search file contents under the project root via Python regex. "
            "Three output modes: files_with_matches (default), content, count.",
            {
                "pattern": {"type": "string", "description": "Python regex"},
                "path": {"type": "string"},
                "glob": {
                    "type": "string",
                    "description": "Filter files by glob, e.g. *.py",
                },
                "output_mode": {
                    "type": "string",
                    "enum": ["files_with_matches", "content", "count"],
                },
                "context": {
                    "type": "integer",
                    "description": "Lines of context before/after each match (content mode only).",
                },
                "case_insensitive": {"type": "boolean"},
                "multiline": {"type": "boolean"},
            },
            ["pattern"],
        ),
        handler=grep_tool,
        permission_category="read_file",
        replace_allowed=True,
    )

    registry.register(
        name="web_search",
        toolset="code",
        schema=_schema(
            "web_search",
            "Search the web (DuckDuckGo) and return up to N {title, url, snippet} results.",
            {
                "query": {"type": "string"},
                "max_results": {
                    "type": "integer",
                    "description": "Default 5, hard cap 10.",
                },
            },
            ["query"],
        ),
        handler=web_search,
        permission_category="network",
        replace_allowed=True,
    )

    # P5-S2 G1: fetch_tool_result — retrieve full body of a truncated
    # tool_result via the ref_id embedded in the "[truncated …]" marker.
    # Companion to B1 (tool_result_truncator); without this the marker
    # advertises a capability that doesn't exist and the LLM gets
    # HallucinationError when it tries to call it.
    registry.register(
        name="fetch_tool_result",
        toolset="code",
        schema=FETCH_TOOL_RESULT_SCHEMA,
        handler=fetch_tool_result_handler,
        permission_category="read_file",
        replace_allowed=True,
    )

    if todo_write_handler is not None and todo_write_schema is not None:
        registry.register(
            name="todo_write",
            toolset="code",
            schema=todo_write_schema,
            handler=todo_write_handler,
            permission_category="read_file",
            replace_allowed=True,
        )

    if agent_handler is not None and agent_schema is not None:
        registry.register(
            name="agent",
            toolset="code",
            schema=agent_schema,
            handler=agent_handler,
            permission_category="read_file",
            replace_allowed=True,
        )

    # WI-C1 (companion-code-skill-upgrade v1, Stage C): agent_parallel —
    # 并发派 2-4 个子代理。toolset="control" 而非 "code"，因为它是 hub-
    # and-spoke 控制流工具（参考 PRD §3 Stage C）。timeout 给 5 分钟兜底，
    # 因为 4 个 subagent × 15 iter × 大模型响应可能拖很久；replace_allowed
    # 留 False 防止 stub 覆盖真实现。
    if agent_parallel_handler is not None and agent_parallel_schema is not None:
        registry.register(
            name="agent_parallel",
            toolset="control",
            schema=agent_parallel_schema,
            handler=agent_parallel_handler,
            # bug fix: "execute_command" 不在合法 PermissionCategory 集（同
            # skill_tools.py P0 bug fix #8 的漏网者）→ gate.check 在 auto-mode
            # 短路前 raise ValueError → agent_parallel 被 except 吞、100% 不执行。
            # 对齐单 agent 工具用 "read_file"：agent_parallel 仅编排子代理，
            # 子代理内部各 tool 仍各自按真实 category check。
            permission_category="read_file",
            source="builtin",
            timeout_seconds=300.0,
            replace_allowed=False,
        )
