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
    """Metadata the LLM sees when deciding whether to call a tool."""
    name: str
    description: str


@runtime_checkable
class Tool(Protocol):
    spec: ToolSpec

    async def invoke(self, **kwargs: object) -> str: ...
