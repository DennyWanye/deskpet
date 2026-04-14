"""MemoryStore Protocol and value objects.

V5 §4.5 envisions semantic memory (bge-m3 + sqlite vector). S2 ships
the simpler short-term conversation memory first — future slices can
add a SemanticMemoryStore alongside without changing this contract.

S14 (V5 §6 threat 5 — user agency over private data) adds a small
management surface on top of the core Protocol: listing turns with
their primary-key id, deleting a single turn, listing known sessions,
and a global ``clear_all``. These sit on the concrete class and on
any decorator (e.g. RedactingMemoryStore) rather than on the Protocol,
so a minimal backend (e.g. in-memory test double) isn't forced to
implement admin endpoints.
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


@dataclass(frozen=True)
class StoredTurn:
    """Turn as returned to the management UI — includes DB id + session.

    Separate from ``ConversationTurn`` so the agent-facing Protocol stays
    minimal (agent doesn't need row ids).
    """
    id: int
    session_id: str
    role: str
    content: str
    created_at: float


@dataclass(frozen=True)
class SessionSummary:
    """One row per session — for the "pick a session to inspect" list."""
    session_id: str
    turn_count: int
    last_message_at: float


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
