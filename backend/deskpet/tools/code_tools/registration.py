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
) -> None:
    """Register glob/grep/web_search statically + todo_write/agent
    using caller-built closures (because they need session-db / llm-shim
    bindings that only exist at startup time).
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
    )

    if todo_write_handler is not None and todo_write_schema is not None:
        registry.register(
            name="todo_write",
            toolset="code",
            schema=todo_write_schema,
            handler=todo_write_handler,
            permission_category="read_file",
        )

    if agent_handler is not None and agent_schema is not None:
        registry.register(
            name="agent",
            toolset="code",
            schema=agent_schema,
            handler=agent_handler,
            permission_category="read_file",
        )
