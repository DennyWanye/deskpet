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
    # --- P4-S16 公开化（之前挂在 _p4_* 私有属性上）---------------------------
    # Embedder / VectorWorker / SessionDB 在 S15 wire-in 时是直接挂私有属性，
    # 这里转成正式 register 路径，避免 getattr(sc, "_p4_xxx") 这种隐式约定。
    "embedder",            # BGE-M3 Embedder (with mock fallback)
    "vector_worker",       # VectorWorker draining embedding queue → vec0
    "session_db",          # P4 canonical L2 SessionDB (state.db)
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
    # --- P4-S16 公开化 -------------------------------------------------------
    embedder: Any | None = None
    vector_worker: Any | None = None
    session_db: Any | None = None

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
