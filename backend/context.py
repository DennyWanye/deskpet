from __future__ import annotations
import copy
from dataclasses import dataclass
from typing import Any

_VALID_SERVICES = frozenset({
    "llm_engine", "asr_engine", "tts_engine",
    "vad_engine", "agent_engine", "memory_store", "tool_router",
    # P2-1-S8: BillingLedger registered so per-session handlers can read the
    # daily-budget ledger without re-plumbing config.
    "billing_ledger",
    # --- P4 Poseidon agent harness (S12 wire-in) -----------------------------
    # Optional: registered only when `config.agent.enabled=true` (ContextAssembler,
    # SkillLoader, MemoryManager) or `config.mcp.enabled=true` (MCPManager).
    # p4_ipc.py handlers tolerate any of these being None via graceful fallback.
    "context_assembler",   # ContextAssembler instance (recent_decisions, assemble)
    "skill_loader",        # SkillLoader with hot-reload + builtin skills
    "memory_manager",      # L1+L2+L3 MemoryManager facade
    "file_memory",         # Direct L1 handle (also reachable via manager.file_memory)
    "mcp_manager",         # MCPManager (stdio/sse/streamable_http clients)
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
    billing_ledger: Any | None = None
    # --- P4 Poseidon slots ---------------------------------------------------
    context_assembler: Any | None = None
    skill_loader: Any | None = None
    memory_manager: Any | None = None
    file_memory: Any | None = None
    mcp_manager: Any | None = None

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
