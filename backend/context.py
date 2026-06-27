# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

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
    "image_worker",        # ImageGenerationWorker (async generate_image)
    "session_db",          # P4 canonical L2 SessionDB (state.db)
    # --- P4-S22 Code Mode ----------------------------------------------------
    "code_mode",           # CodeModeManager — per-base-session enable map
    # --- P5-S1 Pet Supervisor ------------------------------------------------
    "session_activity",    # SessionActivityStore — per-sid recent events + sig window
    "watchdog",            # WatchdogLoop — periodic supervisor scan
    "nudge_queue",         # NudgeQueue — supervisor hint injection queue
    "supervisor",          # SupervisorAgent — LLM-based diagnosis dispatcher
    # --- P5-S2 Self-Healing Harness ------------------------------------------
    "auto_resume",         # AutoResumeOrchestrator — closes supervisor loop
    "tool_circuit_breaker",  # ToolCircuitBreaker — per-(sid, tool) 3-state breaker
    # --- P5-S2 multi-provider-management Phase 2 -----------------------------
    "provider_registry",   # LLMProviderRegistry — owns [[llm.providers]] list
    # --- Stage 2 WI-S2.1a / E3 v2 — MemoryPanel facts view 桥接 ---------------
    "facts_store",         # FactsStore — list_active / mark_forgotten / restore_from_undo
    # --- Companion+Code v1 — SessionGoal + GoalChecker (main.py:1068-1069) ---
    # 2026-05-30 bug fix：之前缺这两项导致 register() 抛 ValueError → boot
    # warning `p4_services_registration_failed` + production UI 弹出红色错误条
    # "Unknown service 'session_goal_store'"。flag `features.goal_mode` 默认
    # OFF 时仍 register(None) 占位（main.py:1068 没 try/except 包）→ 必须在
    # whitelist 里。
    "session_goal_store",  # SessionGoalStore — companion+code goal tracking
    "goal_checker",        # GoalChecker — LLM-based goal completion check
    # --- goal-completion FP-5 WI-4.0 — compaction 接通 AgentLoop ---------------
    # 2026-06-05 真机 bug fix：main.py:1189 无条件 register("context_compressor")，
    # 缺白名单 → 启动 register 抛 ValueError(被 boot try/except 吞成 warning)，
    # 但 chat 派发处 get("context_compressor") 抛 "Unknown service" → code-mode
    # 任务全崩。flag OFF 时仍 register(None) 占位，故必须在白名单里。
    "context_compressor",  # ContextCompressor — WI-4.0 loop 内 compaction
    # --- goal-completion FP-5 接线修复 (2026-06-06) — 5 处跨层 wiring 断裂 -----
    # 独立验收发现 codify hook (main.py:5836/5838/5859) + remount 接线引用了 3 个
    # 未白名单的 service → get() 抛 ValueError("Unknown service") → 被 boot/chat
    # try/except 吞成 debug log → WI-4.1/4.2/4.3 真机完全 no-op。flag OFF（默认）
    # 时 lifespan 仍 register(None) 占位（保 BC + 让 get() 不抛），故必须在白名单里。
    "tool_path_recorder",  # ToolPathRecorder (WI-1.6) — 录工具路径喂 4.3 自创
    "skill_candidate_store",  # SkillCandidateStore (WI-4.3) — pending 候选持久化
    "llm_registry",        # OpenAICompatibleAgentLLM — codify hook 的 chat_with_fallback
    "skill_matcher",       # SkillMatcher (WI-4.1) — embedding 相似度披露 + remount
    # --- superpowers Layer 1B — 偏好记忆（计划/意图，BGE-M3 语义匹配）---------
    "preference_memory",   # PreferenceMemory — plan-confirm 自动确认 + 意图记忆
    # --- Option A (2026-06-05) — 瘦包首启模型下载 ----------------------------
    "model_provisioner",   # ModelProvisioner — 首启从 hf-mirror 下载缺失模型
    # --- 子代理并发驱动 (plans/2026-06-21-subagent-concurrency-driver/) -------
    # flag OFF（默认）时 lifespan 不 register 这些（None 占位无需），但加进白名单
    # 防 get()/register() 抛 "Unknown service"（仿 session_goal_store 注释 :48）。
    "subagent_scheduler",  # SubagentScheduler — lane-aware 有界并发调度
    "subagent_registry",   # SubagentRegistry — 非阻塞 run 记录 + completion queue + 取消
    "team_store",          # TeamStore — spawn_team 共享任务池/mailbox/permission
    "task_graph_store",    # TaskGraphStore — DAG 依赖排序任务图
    # --- WI-OH-4 记忆 self-curation nudge (plans/2026-06-21-ppt-deepresearch-pro) ---
    # flag memory.v2.curation_nudge OFF（默认）时 lifespan 不 register（None 占位
    # 无需），但加进白名单防 get()/register() 抛 "Unknown service"（仿上方注释）。
    "memory_curator",      # MemoryCurator — agent 主动判断该不该长期记
    # --- WI-TG-2 审批 UX 聚合 (plans/2026-06-22-context-and-agent-optimization) ---
    # PermissionGate 注册成 service 让 p4_ipc 的只读「列 pending」接口拿到它，
    # 供 ApprovalCenterPanel 聚合展示。只读 — 不改 gate 决策路径。
    "permission_gate",     # PermissionGate — 只读 list_pending 供审批聚合面板
    # --- 七步问题处理流水线 (plans/2026-06-24-problem-handling-pipeline-maoxuan/) ---
    # flag features.problem_pipeline.enabled OFF 时 lifespan 仍 register(None)
    # 占位（见 main.py lifespan 的 else 分支）—— 否则 get()/register() 抛
    # "Unknown service"（仿 session_goal_store 注释 :48 / context_compressor :57 的占位约定）。
    "problem_pipeline",                  # ProblemHandlingPipeline（PRE-LOOP 编排器）
    "pipeline_evidence_gate",            # EvidenceGate（Step2 取证门，build_agent caller 透传）
    "pipeline_self_check_gate",          # 预留：第一期由 build_agent 内构造，service 仅占位
    "pipeline_convergence_controller",   # 预留：第一期由 AgentLoop 内构造，service 仅占位
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
    # --- P4-S22 Code Mode ----------------------------------------------------
    code_mode: Any | None = None
    # --- P5-S1 Pet Supervisor ------------------------------------------------
    session_activity: Any | None = None
    watchdog: Any | None = None
    nudge_queue: Any | None = None
    supervisor: Any | None = None
    # --- P5-S2 Self-Healing Harness ------------------------------------------
    auto_resume: Any | None = None
    tool_circuit_breaker: Any | None = None
    # --- P5-S2 multi-provider-management Phase 2 -----------------------------
    provider_registry: Any | None = None
    # --- Stage 2 WI-S2.1a / E3 v2 -------------------------------------------
    facts_store: Any | None = None
    # --- Companion+Code v1 --------------------------------------------------
    session_goal_store: Any | None = None
    goal_checker: Any | None = None
    # --- superpowers Layer 1B ------------------------------------------------
    preference_memory: Any | None = None
    # --- Option A — 瘦包首启模型下载 -----------------------------------------
    model_provisioner: Any | None = None
    # --- goal-completion FP-5 接线修复 (2026-06-06) -------------------------
    tool_path_recorder: Any | None = None
    skill_candidate_store: Any | None = None
    llm_registry: Any | None = None
    skill_matcher: Any | None = None
    # --- 子代理并发驱动 (plans/2026-06-21-subagent-concurrency-driver/) -------
    subagent_scheduler: Any | None = None
    subagent_registry: Any | None = None
    team_store: Any | None = None
    task_graph_store: Any | None = None
    # --- WI-OH-4 记忆 self-curation nudge ------------------------------------
    memory_curator: Any | None = None
    # --- WI-TG-2 审批 UX 聚合 -------------------------------------------------
    permission_gate: Any | None = None
    # --- 七步问题处理流水线 (plans/2026-06-24-problem-handling-pipeline-maoxuan/) ---
    problem_pipeline: Any | None = None
    pipeline_evidence_gate: Any | None = None
    pipeline_self_check_gate: Any | None = None
    pipeline_convergence_controller: Any | None = None

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
