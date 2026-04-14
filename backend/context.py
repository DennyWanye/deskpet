from __future__ import annotations
import copy
from dataclasses import dataclass
from typing import Any

_VALID_SERVICES = frozenset({
    "llm_engine", "asr_engine", "tts_engine",
    "vad_engine", "agent_engine", "memory_store", "tool_router",
})

@dataclass
class ServiceContext:
    llm_engine: Any | None = None
    asr_engine: Any | None = None
    tts_engine: Any | None = None
    vad_engine: Any | None = None
    agent_engine: Any | None = None
    memory_store: Any | None = None
    tool_router: Any | None = None

    def register(self, name: str, provider: Any) -> None:
        if name not in _VALID_SERVICES:
            raise ValueError(f"Unknown service '{name}'. Valid: {sorted(_VALID_SERVICES)}")
        setattr(self, name, provider)

    def create_session(self) -> ServiceContext:
        return copy.deepcopy(self)

    def get(self, name: str) -> Any | None:
        if name not in _VALID_SERVICES:
            raise ValueError(f"Unknown service '{name}'. Valid: {sorted(_VALID_SERVICES)}")
        return getattr(self, name, None)
