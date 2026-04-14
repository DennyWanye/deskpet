"""Demo tool: returns current local time as ISO-8601 string.

Deliberately trivial — exists to verify the whole tool routing pipeline
before investing in stateful or external-API tools.
"""
from __future__ import annotations

from datetime import datetime

from tools.base import Tool, ToolSpec


class GetTimeTool:
    spec = ToolSpec(
        name="get_time",
        description="Returns the current local date and time in ISO-8601 format.",
    )

    async def invoke(self, **kwargs: object) -> str:
        return datetime.now().isoformat(timespec="seconds")


# Module-level singleton — convenient for registry.register() at startup
get_time_tool: Tool = GetTimeTool()
