"""Tool registry — central catalog injected into ToolUsingAgent."""
from __future__ import annotations

from tools.base import Tool, ToolSpec


class ToolRegistry:
    """Name-indexed collection of Tool instances."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        name = tool.spec.name
        if name in self._tools:
            raise ValueError(f"Tool '{name}' already registered")
        self._tools[name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_specs(self) -> list[ToolSpec]:
        return [t.spec for t in self._tools.values()]

    def prompt_hint(self) -> str:
        """System-prompt snippet: persona identity + tool listing.

        The persona block prevents the LLM from hallucinating a model
        identity it picked up from conversation history (e.g. claiming
        to be "Gemma" after seeing earlier local-model replies in the
        memory store). The identity is deliberately model-agnostic — it
        says "desktop pet assistant", not "qwen" or "gemma".
        """
        if not self._tools:
            return ""
        parts: list[str] = [
            "You have access to these tools:",
        ]
        for spec in self.list_specs():
            parts.append(f"- {spec.name}: {spec.description}")
        parts.append(
            "To invoke a tool, emit `<tool>NAME</tool>` in your reply; "
            "the tool's result will be appended to your message."
        )
        return "\n".join(parts)
