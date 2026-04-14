"""Tool protocol for the S3 tool routing layer.

MVP: tools are zero-arg for now (get_time doesn't need params). Protocol
keeps `invoke(**kwargs)` so we can add parameter parsing without breaking
the contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ToolSpec:
    """Metadata the LLM sees when deciding whether to call a tool.

    ``requires_confirmation`` marks tools that touch user state (delete file,
    run script, open URL). When True, the ToolUsingAgent must obtain explicit
    user confirmation (Tauri dialog via control channel) before ``invoke``.
    Defaults to False so existing tools keep their behaviour.
    """
    name: str
    description: str
    requires_confirmation: bool = False


@runtime_checkable
class Tool(Protocol):
    spec: ToolSpec

    async def invoke(self, **kwargs: object) -> str: ...
