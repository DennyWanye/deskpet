"""MemoryStore Protocol and value objects.

V5 §4.5 envisions semantic memory (bge-m3 + sqlite vector). S2 ships
the simpler short-term conversation memory first — future slices can
add a SemanticMemoryStore alongside without changing this contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ConversationTurn:
    """One exchange leg — `role` is 'user' or 'assistant'."""
    role: str
    content: str
    created_at: float  # unix timestamp, for ordering


@runtime_checkable
class MemoryStore(Protocol):
    """Per-session conversation memory. Contract:
    - get_recent returns turns ordered oldest → newest (ready to prepend).
    - append persists one turn; idempotency is NOT guaranteed.
    - clear removes all turns for session_id (test/reset path).
    """

    async def get_recent(
        self, session_id: str, limit: int = 10
    ) -> list[ConversationTurn]: ...

    async def append(self, session_id: str, role: str, content: str) -> None: ...

    async def clear(self, session_id: str) -> None: ...
