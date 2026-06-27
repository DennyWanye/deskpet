# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S22 Code mode tools.

Five tools registered alongside the seven OS tools when Code mode is
active. Companion mode never sees them — registration is gated by
``code_mode.is_enabled(session_id)`` at registry-emit time, so the LLM
won't even know they exist outside Code mode.

  * glob  — find files by pattern (pathlib.rglob), mtime-sorted
  * grep  — content search via Python re (no external rg dependency)
  * todo_write — task list state, persisted to SessionDB + pushed to UI
  * web_search — DuckDuckGo HTML scrape
  * agent — spawn a nested AgentLoop with read-only tools
"""
from __future__ import annotations

from .glob_tool import glob_tool
from .grep_tool import grep_tool
from .todo_write_tool import build_todo_write_tool
from .web_search_tool import web_search
from .agent_tool import build_agent_tool
from .clarify_tool import build_ask_clarification_tool
from .registration import register_code_tools

__all__ = [
    "glob_tool",
    "grep_tool",
    "build_todo_write_tool",
    "web_search",
    "build_agent_tool",
    "build_ask_clarification_tool",
    "register_code_tools",
]
