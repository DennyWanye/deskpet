# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations
import json
import logging
import os
import shutil
import sys
import tomli
from pathlib import Path
from dataclasses import dataclass, field, fields as dc_fields
from typing import Optional

import paths as _paths

logger = logging.getLogger(__name__)


# ─── P6 agent-loop-refactor — process-wide feature flag ─────────
#
# DEPRECATED P6 Phase 6 — flag is permanently ON by default; the new
# AgentLoop/ContextManager/TerminationGate path is the only path. The
# function is kept for one release as an opt-OUT switch (set
# ``P6_ENABLE_GATE=0`` to force the (now-removed) legacy path's behaviour
# — but legacy code is gone, so 0 just disables auto-construction of
# defaults inside ``AgentLoop``; callers MUST then pass their own gate
# and ctx_manager explicitly, otherwise behaviour is undefined).
# Remove this function in P7 once we're sure no in-the-wild deployment
# is setting the env var.
_P6_TRUTHY = frozenset({"1", "true", "yes"})
_P6_FALSY = frozenset({"0", "false", "no"})


def is_p6_gate_enabled() -> bool:
    """P6 agent-loop-refactor — process-wide feature flag.

    Reads ``P6_ENABLE_GATE`` from the environment on every call so the
    flag can be flipped at runtime (no restart needed) — useful in tests
    and for quick rollback if the new loop misbehaves in production.

    Phase 6 default flip: **unset env var now returns True**. Only an
    explicit falsy value (``"0"``, ``"false"``, ``"no"``, empty string,
    case-insensitive, whitespace-stripped) returns False. Truthy values
    are still accepted for explicitness.

    Lives in ``config`` so callers anywhere in the backend can import it
    without pulling agent/loop dependencies through ``main``.
    """
    raw = os.environ.get("P6_ENABLE_GATE")
    if raw is None:
        # Phase 6: default ON.
        return True
    stripped = raw.strip().lower()
    if stripped in _P6_FALSY or stripped == "":
        return False
    # Anything else (including the historical truthy values) → True.
    return True


def resolve_cloud_api_key() -> str | None:
    """P2-1-S3: source of truth for the cloud LLM API key.

    Tauri reads the user's key from the OS credential store on launch
    and injects it as ``DESKPET_CLOUD_API_KEY``. We intentionally do NOT
    fall back to ``config.llm.cloud.api_key`` — a plaintext value in the
    TOML is a migration leftover that ``load_config`` already warns about.

    Returning ``None`` (not ``""``) lets callers use plain truthiness to
    decide whether the cloud provider should be constructed at all.

    Lives in ``config`` (not ``main``) so tests can import it without
    pulling the heavy provider/model dependencies through ``main.py``.
    """
    val = os.environ.get("DESKPET_CLOUD_API_KEY")
    if not val:
        return None
    return val

@dataclass
class BackendConfig:
    host: str = "127.0.0.1"
    port: int = 8100
    log_level: str = "INFO"

@dataclass
class LLMEndpointConfig:
    """Per-endpoint config (local or cloud). Mirrors OpenAICompatibleProvider ctor."""
    model: str = "gemma4:e4b"
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"
    temperature: float = 0.7
    max_tokens: int = 2048


@dataclass
class LLMRoutingConfig:
    strategy: str = "cloud_first"
    daily_budget_cny: float = 10.0
    local: LLMEndpointConfig = field(default_factory=LLMEndpointConfig)
    cloud: LLMEndpointConfig | None = None

@dataclass
class ASRConfig:
    provider: str = "faster-whisper"
    model: str = "large-v3-turbo"
    device: str = "cuda"
    compute_type: str = "float16"
    # P2-2-F1: hotwords list for faster-whisper logit bias. Joined with
    # spaces at provider init. Empty list = no bias (current behaviour).
    # Keeps short-audio phrases like "讲个笑话" from being misheard as
    # "一个消化" by nudging beam search away from pinyin-adjacent high-
    # frequency words.
    hotwords: list[str] = field(default_factory=list)
    # P3-S1: subfolder under paths.model_root() containing the bundled
    # faster-whisper model. Empty → provider falls back to HuggingFace
    # cache / model name resolution.
    model_dir: str = "faster-whisper-large-v3-turbo"

@dataclass
class TTSConfig:
    provider: str = "edge-tts"
    voice: str = "zh-CN-XiaoyiNeural"
    # P3-S1: bare subfolder name under paths.model_root(). Was
    # "./assets/cosyvoice2" (relative-to-CWD, fragile under PyInstaller).
    # load_config() still accepts the legacy "./assets/..." form and
    # auto-strips it with a WARNING.
    model_dir: str = "cosyvoice2"

@dataclass
class VADConfig:
    threshold: float = 0.5
    min_speech_ms: int = 250
    min_silence_ms: int = 500

@dataclass
class VoiceConfig:
    """P2-2-M3: TTS-phase barge-in overrides.

    Echo suppression is a time-domain state machine (see BargeInFilter):
      IDLE        — speech_start → immediate barge-in allowed
      TTS_PLAYING — speech_start must sustain >= min_speech_ms_during_tts
                    AND VAD uses the raised threshold (vad_threshold_during_tts)
      COOLDOWN    — for tts_cooldown_ms after TTS ends, any speech_start is
                    ignored; prevents the pet's own audio tail from retriggering

    The "normal" threshold / min_speech_ms live in [vad] — this section only
    holds the TTS-phase overrides so pipeline can swap them dynamically.
    """
    vad_threshold_during_tts: float = 0.65
    min_speech_ms_during_tts: int = 400
    tts_cooldown_ms: int = 300

@dataclass
class MemoryV2FactsConfig:
    """``[memory.v2.facts]`` — facts 抽取调参（记忆系统升级）。"""
    min_user_chars: int = 8       # 字数采样门，取代 facts.py 硬编码 <8
    facts_weight: float = 0.2     # facts 路进 RRF 的权重
    model_override: str = ""      # 留空 = 用主 LLM
    # Stage 2 D8 v2：entity 路 RRF 权重；v1 是 0.15，保守降为 0.10。
    entity_weight: float = 0.10
    # 2026-06-27 内容哈希幂等去重（FactExtractor Layer 1）：TTL 与缓存上限。
    content_dedup_ttl_s: int = 3600        # 相同内容近期(秒)抽过则跳过重抽
    content_dedup_cache_max: int = 256     # in-process 内容哈希 LRU 上限


@dataclass
class MemoryV2ForgetConfig:
    """``[memory.v2.forget]`` — memory_forget 工具子段（Stage 2 D5 v2）。

    自然语言模式（query="...") 默认禁用：开放面是提示注入攻击面，需
    用户在 config 里显式启用才生效。fact_id 模式始终开放。

    子段名用 ``forget`` 而非 ``memory_forget`` —— 后者与父表的同名 flag
    冲突（同一个 key 不能既是 bool 又是 dict）。
    """
    enable_natural_language: bool = False


@dataclass
class MemoryV2Config:
    """``[memory.v2]`` — memory-v2 各模块的 feature flag。

    全部默认 False → 行为与第一代"三层 + RRF"逐字节一致。每个 flag 单独
    控制一个 v2 模块的接入（Strangler-Fig：关 flag 即回退第一代）。
    """
    feedback_loop: bool = True        # WI-M1.1 用户 thumbs-up 回路（测试阶段出厂点亮）
    facts_extract: bool = True        # WI-M1.2 写入端事实抽取（测试阶段出厂点亮）
    rerank: bool = True               # WI-M1.3 cross-encoder 重排（测试阶段出厂点亮；无 GPU 时 CPU 跑慢但不崩）
    enhanced_retriever: bool = True   # WI-M1.4 facts 进 RRF（测试阶段出厂点亮）
    chunking: bool = True             # WI-M1.5 长消息切块（测试阶段出厂点亮）
    query_rewrite: bool = True        # WI-M1.5 短查询改写（测试阶段出厂点亮）
    workspace_memory: bool = False    # WI-M1.6 code 工作记忆（B 表：code 模式专属，主线不开）
    reflection: bool = True           # WI-M1.7 反思 / skill memory（测试阶段出厂点亮）
    # Stage 2 新增 4 flag（PRD §2.1 G1-G5 / §3 D16）：
    cross_key_merge: bool = True          # WI-S2.1a 跨 key 矛盾治理（测试阶段出厂点亮）
    memory_forget: bool = True            # WI-S2.1a 显式遗忘工具（fact_id 模式；NL 模式仍由 forget.enable_natural_language 独立守门）
    entity_path: bool = True              # WI-S2.2 entity 索引检索路（测试阶段出厂点亮）
    episodic_to_semantic: bool = True     # WI-S2.4 summary 抽 facts（测试阶段出厂点亮）
    # FP-4 WI-3.1：goal / decision / constraint 类别抽取
    goal_facts: bool = True               # WI-3.1 goal/decision/constraint 记忆抽取（测试阶段出厂点亮）
    # FP-4 WI-3.3 / WI-OH-2 决策①：PreferenceMemory 半衰期衰减默认开。
    # 桌宠自动淡忘老偏好（老条目 effective 分沉底）；pinned 偏好跳衰减。
    # 硬前置：用户保留偏好的 pin 入口（memory_write pinned=True / memory_pin
    # WS verb）与本默认值同批可用，确保用户有逃生口。
    pref_decay: bool = True               # WI-OH-2 决策① 默认开（原 WI-3.3 默认 False）
    # FP-4 WI-3.4：写入分级 light 快路（默认 False）
    # False → skip_embed 强制 False，所有消息都进 L3 向量（当前行为不变）。
    # True  → 调用方可传 skip_embed=True 跳过 L3 embedding，仅走 L2（消息
    #         仍入 messages 表 + FTS5 trigger；embedding IS NULL；FTS/recency
    #         仍可召回）。适用于语音 VAD tick / 截屏低信息判定等高频低信息流。
    # 实际生效取决于调用方是否传 skip_embed=True；flag 关闭时任何 skip_embed=True
    # 应被调用方屏蔽（有效 skip_embed = flag AND caller_intent）。
    light_write: bool = True              # WI-3.4 light 快路开关（测试阶段出厂点亮；实际生效仍需调用方传 skip_embed）
    # FP-4 WI-3.2：人格画像主动注入 Component（默认 False，dev 先开）。
    # False → PreferenceProfileComponent 返回空 Slice → bundle 字节级等同当前（BC）。
    persona_inject: bool = True           # WI-3.2 preference profile injection（测试阶段出厂点亮）
    # FP-4 B-10：goal→facts 双写钩（默认 False）。
    # False → bind_on_goal_set 不接电 → goal_store.set() BC。
    goal_facts_hook: bool = True          # B-10 goal→facts double-write hook（测试阶段出厂点亮）
    # WI-OH-4：记忆 self-curation nudge（agent 主动判断该不该记，对标 hermes
    # 周期性自省 + openhuman self-curation）。默认 False = 字节级 BC：
    # flag OFF → MemoryCurator 不构造、agent_loop 不调 nudge、不写 facts。
    curation_nudge: bool = True           # WI-OH-4 记忆自策展 nudge 开关（测试阶段出厂点亮）
    # WI-OH-4 频率门控：每 N 个回合触发一次 nudge（对齐 hermes「周期性」）。
    # 仅在 curation_nudge=True 时生效。
    curation_nudge_every_n_turns: int = 8
    # WI-CC-5：auto-memory learnings（procedural 类）。与 OH-4 curator 合并：
    # 允许 curator 输出 category='learning'（如「用户上次 PPT 要深色主题」「生成
    # 周报的步骤」），写 facts 表（慢衰减）+ 由 preference_profile 注入。
    # 默认 False = 字节级 BC：flag OFF → curator 不产 learning、注入不含 learning。
    # 仅在 curation_nudge=True 且 auto_learnings=True 时产 learning。
    auto_learnings: bool = True           # WI-CC-5 auto-memory learnings 开关（测试阶段出厂点亮；仅 curation_nudge=True 时产 learning）
    # 2026-06-27 内容哈希幂等去重（FactExtractor Layer 1）：相同内容跳过重抽，防重复事实累积。
    # 测试阶段出厂点亮（与 facts_extract 同档）。OFF 时 FactExtractor 不启用内容去重 = 字节级 BC。
    extract_content_dedup: bool = True
    facts: MemoryV2FactsConfig = field(default_factory=MemoryV2FactsConfig)
    forget: MemoryV2ForgetConfig = field(
        default_factory=MemoryV2ForgetConfig,
    )


@dataclass
class MemoryConfig:
    # P3-S7: empty string = "auto-resolve to <user_data_dir>/data/memory.db".
    # Previously defaulted to "./data/memory.db" which was CWD-relative and
    # fragile under PyInstaller (CWD = wherever Tauri launched us from).
    # load_config() rewrites empty/relative values to the user data dir;
    # explicit absolute paths in config.toml pass through untouched.
    db_path: str = ""
    embedding_model: str = "bge-m3"
    # 记忆系统升级 —— [memory.v2] 子表。_load_section 不递归解析嵌套
    # dataclass，故 load_config 把 v2 子表 pop 出来单独构建（见 _load_memory_v2）。
    v2: MemoryV2Config = field(default_factory=MemoryV2Config)


class ConfigError(ValueError):
    """启动期 invariant 违反；错误码见 PRD §3 D10 表（VG-INVARIANT-{0..6}）。

    Distinct from generic ``ValueError`` so callers (load_config 调用方)
    可以单独 catch + 给用户友好提示。
    """


@dataclass
class ToolsLastMileConfig:
    """``[tools.last_mile]`` — D1-D4 / D9 flag。

    全 False / 默认值时与现状字节级一致（PRD §3 D10 末段 + TG-12 T12-1）：
      - ``artifact_envelope=False`` 时 tool_result 不得 emit ``artifacts`` 键。
      - ``frontend_artifact_card=False`` 时 MessageBubble DOM 树未变化。
    """
    artifact_envelope: bool = False         # D1 信封包装
    frontend_artifact_card: bool = False    # D2 前端新卡片
    tauri_artifact_ops: bool = False        # D3 Tauri shell 桥
    default_artifact_dir: str = ""          # D4 空 = 走旧 tempdir
    outline_preview_default: bool = False   # D9 PPT outline 预览
    artifact_dir_retention_days: int = 30


@dataclass
class ToolsVerifierConfig:
    """``[tools.verifier]`` — D5-D8 flag + D6/D11 协作配置。

    invariant 见 PRD §3 D10 + _validate_flag_invariants：
      - ``verify_gate_mode != "off"`` 必须 ``emit_receipts=True``，否则 ledger
        永远空，所有 claim 都 unmatched，会无脑阻塞所有 end_turn。
      - ``ephemeral_subagent_model`` 缺省时默认 ``"haiku"``（N5）。
    """
    # WI-HM-1 决策①：自我纠错闭环出厂默认点亮（非字节级 BC，强回归+真机兜底）。
    # 三个默认值是一个原子改动（VG-INVARIANT-1 硬连锁见 _validate_flag_invariants）：
    #   verify_gate_mode != "off" 必须 emit_receipts=True，否则 ledger 永远空 →
    #   ConfigError VG-INVARIANT-1（或所有 end_turn 被无脑阻塞）。
    # 2026-06-23 升级 strict（完成 decision① 目标"全档开 strict"）：真机已确认
    # strict 在无 claim 纯闲聊下不误阻塞 end_turn（"你好呀今天天气"等多轮 stop_reason=
    # end_turn 正常收尾 + goal/task 回合也放行，见 exec/B1 真测）。前置满足故由 shadow
    # 升 strict——verify 真守门拦截虚报完成→reflection 重试。若后续某档误阻塞可退 shadow。
    emit_receipts: bool = True
    verify_gate_mode: str = "strict"               # off | shadow | strict
    extractor_fallback_enabled: bool = True        # D6 二级 LLM fallback
    ephemeral_subagent_model: str = "haiku"        # D6 第 3 次失败救援模型
    run_build: bool = False                        # D7 build verifier
    run_tests: bool = False                        # D7 test verifier
    claim_patterns_file: str = "verify/claim_patterns.yaml"
    # v3 WI-T2.1 接电 + M5 ToolsConfig 扩展：build_agent 工厂从这里读 nudge 上限
    # （PRD §3 D6：failure_count == 3 时调度 ephemeral；默认 2 表示连续 2 次
    # unmatched 才触发救援）
    max_verify_nudges: int = 2
    # WI-2.1 structured reflection: when True, _REFLECTION_INSTRUCTION is
    # appended to verify-gate rebound + selfcheck tier2/tier3 system messages.
    # WI-HM-1 决策①：出厂默认点亮（原 False=BC）。
    structured_reflection: bool = True
    # WI-2.4 external evaluator: cross-persona quality judge for high-consequence
    # goals (prod off / dev on). 测试阶段出厂点亮（高后果异体评分）。
    external_evaluator: bool = True
    # Provider key to use for the evaluator (default = reuse main LLM provider).
    # "default" means: reuse build_agent's local_llm with evaluator system persona.
    evaluator_provider: str = "default"


@dataclass
class ToolsConfig:
    """``[tools]`` 父表，含 last_mile / verifier 两个子表 + v3 5 字段。

    _load_section 平铺解析，无法直接处理嵌套子 dataclass —— load_config 把
    子表 pop 出来单独构建（_load_tools，同 _load_memory_v2 模式）。

    WI-T5.1 v3 新增 5 字段（PRD §3.5 G6 + TDD §A12）：

    * ``disabled_toolsets`` — **★v3 默认 strict**：列出的 toolset 既不出现
      在 LLM schemas 也无法 execute_tool（双层挡，PRD D14 P1-5 反 v1 反模式）。
      Silent breaking change 风险见 R12 — release notes 提醒用户从 schema-only
      迁移到 strict 默认前显式过迁移路径。
    * ``disabled_toolsets_schema_only`` — opt-in 边缘场景：仅 LLM 看不到但
      execute_tool 仍可调（编排器/测试夹具用）。
    * ``dangerous_tools_allowlist`` — 非空时仅 allowlist 中的 dangerous=True
      工具会出现在 schemas（默认空 = 沿用 UI 确认 popup 流程）。
    * ``default_timeout_seconds`` — ToolSpec.timeout_seconds 未指定时的兜底
      （ToolSpec 默认 60，但 execute_tool 读 cfg 兜底防忘配）。
    * ``strict_unknown_toolset`` — True → ``disabled_toolsets`` 含 typo
      (registry 未知 toolset 名) 时启动 fail-fast；False 仅 warn。
    """
    last_mile: ToolsLastMileConfig = field(default_factory=ToolsLastMileConfig)
    verifier: ToolsVerifierConfig = field(default_factory=ToolsVerifierConfig)
    # ─── WI-T5.1 v3 ─────────────────────────────────────────────
    disabled_toolsets: list[str] = field(default_factory=list)
    disabled_toolsets_schema_only: list[str] = field(default_factory=list)
    dangerous_tools_allowlist: list[str] = field(default_factory=list)
    default_timeout_seconds: float = 60.0
    strict_unknown_toolset: bool = False


@dataclass(frozen=True)
class BillingConfig:
    """P2-1-S8 BillingLedger config.

    `db_path` is computed at load-time from the MemoryConfig data dir so we
    keep the two SQLite files side-by-side under `./data/`.

    `tz` is the IANA timezone name used for daily rollover. Defaults to
    Asia/Shanghai (product targets Chinese users); deployments overseas
    can override via [billing] tz = "America/Los_Angeles" etc.
    """
    daily_budget_cny: float = 10.0
    unknown_model_price_cny_per_m_tokens: float = 20.0
    pricing: dict[str, float] = field(default_factory=dict)
    db_path: Path = field(default_factory=lambda: Path("./data/billing.db"))
    tz: str = "Asia/Shanghai"

    @classmethod
    def from_toml(cls, data: dict, db_dir: Path) -> "BillingConfig":
        b = data.get("billing", {}) or {}
        return cls(
            daily_budget_cny=float(b.get("daily_budget_cny", 10.0)),
            unknown_model_price_cny_per_m_tokens=float(
                b.get("unknown_model_price_cny_per_m_tokens", 20.0)
            ),
            pricing=dict(b.get("pricing", {}) or {}),
            db_path=db_dir / "billing.db",
            tz=str(b.get("tz", "Asia/Shanghai")),
        )


@dataclass
class SkillsAutoDisclosureConfig:
    """``[skills.auto_disclosure]`` — WI-4.1 二级披露 feature flag + tuning.

    Default ``enabled=False`` → byte-identical to pre-WI-4.1 behavior.
    Set ``enabled=True`` in config.toml (or ``[features] …``) to activate
    automatic skill body inlining. 测试阶段出厂点亮。
    """
    enabled: bool = True
    strong_threshold: float = 0.55   # cos-sim threshold for "strong match"
    budget_tokens: int = 8000        # total token budget for inlined bodies
    per_skill_max_tokens: int = 2000  # single-skill body truncation cap


@dataclass
class SkillsCodifyConfig:
    """``[skills.codify]`` — WI-4.3 技能自创闭环 feature flag + rate-limit.

    Default ``enabled=False`` (dev on / prod off).
    Set ``[skills.codify] enabled = true`` in config.toml to activate.
    ``max_candidates_per_day`` caps how many pending candidates can be
    generated per calendar day (防打扰). 测试阶段出厂点亮。
    """
    enabled: bool = True
    max_candidates_per_day: int = 3


@dataclass
class SkillsConfig:
    """``[skills]`` top-level config table (WI-4.1+)."""
    # WI-5: triggered background knowledge snippets. 测试阶段出厂点亮。
    knowledge_enabled: bool = True
    auto_disclosure: SkillsAutoDisclosureConfig = field(
        default_factory=SkillsAutoDisclosureConfig
    )
    # WI-4.3 技能自创闭环 — dev on / prod off by default.
    codify: SkillsCodifyConfig = field(default_factory=SkillsCodifyConfig)


@dataclass
class ProblemPipelineConfig:
    """``[features.problem_pipeline]`` — 七步问题处理流水线（plans/2026-06-24-...，仅 Companion 主线）。

    决策1（测试环境）：出厂即开全量验证——enabled 默认 **True**，各子 flag 默认 on；
      flag 仅作 kill-switch + 单步调试开关（出问题时关某步或整条 enabled=false 一键回退）。
      **不做 shadow→light→strict 灰度档**：self_check 是 bool，严格度由 SelfCheckGate 内部按
      problem_type 选（debug/creation 严、factual 轻、chitchat 跳过）。
    决策3（模型可配）：analysis_model / self_check_model 留空 = 复用主 LLM gpt-5.5（中转站只保证主模型），
      不硬依赖 haiku；非空才解析独立 model（失败回退主 LLM）。
    """
    enabled: bool = True                       # 总开关（kill-switch）：false → 整条短路回退现有链路
    intent_triage: bool = True                 # Step1+3 合并预分析（意图 + 主要矛盾）
    intent_clarify_threshold: float = 0.7      # 歧义澄清阈值
    analysis_timeout_s: float = 45.0           # 预分析 LLM 超时（真机修正：deepseek-v4-pro thinking 慢，需给足时间；原 30s 仍偶超时）
    evidence_gate: bool = True                 # Step2 取证门控
    evidence_max_nudges: int = 2               # 取证 nudge 上限
    evidence_investigative_tools: list[str] = field(default_factory=list)  # 空=用模块默认白名单
    plan_companion_enabled: bool = True        # Step4 为 Companion 主线新增 plan（code 模式不动）
    analysis_model: str = "deepseek-v4-pro"    # 决策3：意图+矛盾分析模型（relay 实测 stream+json_schema 稳，规避 gpt-5.5 间歇 502）
    self_check: bool = True                    # Step6 自检总开关（bool；严格度内部按 problem_type 选）
    self_check_model: str = ""                 # 决策3：异体自检评分模型（留空=主 LLM gpt-5.5）
    self_check_heterogeneous: bool = True      # 失败 N 次后启异体（fresh-context 子代理）评分
    convergence_report_on_stop: bool = True    # Step7 止损报告
    observability_events: bool = True          # 是否发 <标签> WS 事件


@dataclass
class FeaturesConfig:
    """``[features]`` 父表 — Companion + Code 升级 v1 (plans/2026-05-25-...).

    全 flag 默认 OFF；OFF 状态与现状字节级一致。每个 flag 守护一组改动:

    * ``slash_commands`` — 启 / 命令解析（WI-A 系列）。前端 InputBar 见到 `/`
      前缀时发 ``slash_command`` WS 消息，后端 dispatcher 路由到 SkillLoader /
      goal_store / builtin handlers。OFF 时 / 当普通 chat 文本。
    * ``goal_mode`` — 启 ``/goal <text>`` 长期目标 + AgentLoop 末轮 goal_checker
      check + 未达成自动 continue（WI-B 系列）。OFF 时 SessionGoalStore 不构造。
    * ``agent_parallel`` — 启 ``agent_parallel`` 工具暴露给 LLM（WI-C 系列）。
      OFF 时工具不出现在 schemas，子代理并发能力不可用。
    * ``plan_confirm_gate`` — superpowers Layer 1A/1B 决策2：code 模式非平凡任务
      先出 plan（已有 maybe_extract_plan）并**等用户点[执行]确认再跑 ReAct**（硬门）。
      OFF（默认）时 plan 仍展示但 auto-confirm（现状字节级一致）。前端需配合
      渲染 [执行]/[取消] 按钮（chat_v2_plan.awaiting_confirm + plan_confirm WS）。
    * ``preference_memory`` — superpowers Layer 1B：BGE-M3 语义偏好记忆。计划记忆
      让 plan-confirm 硬门对"语义相似且以往批准过"的任务**自动确认**（决策2 的
      "记下来后续直接做"）。OFF（默认）时不构造 PreferenceMemory，门每次都等确认。
    * ``plan_read_only`` — WI-CC-2 规划期**物理只读**。ON 且 plan-confirm 硬门挂起
      （展示 plan、等用户点[执行]）期间，registry.execute_tool 对所有写/执行类
      permission_category（write_file/desktop_write/shell/skill_install）的工具
      返回「规划期只读」deny，**不执行**；只读工具（read_file/network 等）照常放行。
      用户点[执行]→解禁。OFF（默认）= 规划期不切只读、写类工具照常（字节级 BC）。
      注：需 plan_confirm_gate 一并 ON 才有挂起窗口可锁。
    * ``relay_managed_provider`` — WI-3/WI-6（plans/2026-06-25-relay-local-apikey-provider）。
      **默认 ON**（与前端 `relayConfig.RELAY_MANAGED_PROVIDER` 镜像）。relay 登录后
      把账户收编进 `LLMProviderRegistry` 作为一条 `source="relay"` 的正常 provider
      （前端 `relayProviderRegistration` 发 ``settings_providers_ensure``）。本后端
      flag 是 kill-switch：OFF 时后端**拒绝** ``settings_providers_ensure``（回退到
      旧 `relayProviderBridge` 旁路 = 前端 flag 也须同步 OFF）。主控由前端 flag 决定
      （它决定发不发 ensure）；本 flag 提供后端侧防御性兜底。
    """
    slash_commands: bool = True            # 测试阶段出厂点亮
    goal_mode: bool = True                 # 测试阶段出厂点亮
    agent_parallel: bool = True            # 测试阶段出厂点亮
    plan_confirm_gate: bool = True         # 测试阶段出厂点亮
    preference_memory: bool = True         # 测试阶段出厂点亮
    plan_read_only: bool = False           # B 表：归 WI-1.2 自治档统一处理（开了 plan 期禁写，与效率优先冲突）
    relay_managed_provider: bool = True
    # --- 子代理并发驱动（plans/2026-06-21-subagent-concurrency-driver/）---------
    # 全默认 OFF；OFF 时新代码 short-circuit，agent_parallel 退回扁平 gather（字节级 BC）。
    #   subagent_driver       — 总开关：事务分型(task_kinds)路由 + 有界调度(scheduler)接入
    #   agent_team            — 暴露 spawn_team LLM 工具 + 构造 TeamStore/TaskGraphStore
    #   subagent_nonblocking  — 非阻塞 spawn_subagents/await_subagents + completion queue 回灌
    subagent_driver: bool = True           # 测试阶段出厂点亮
    agent_team: bool = True                # 测试阶段出厂点亮
    subagent_nonblocking: bool = True      # 测试阶段出厂点亮
    # WI-4.0 compaction: wire ContextCompressor into AgentLoop.
    # WI-6 (compaction-bestpractice-upgrade, 2026-06-16): 默认翻 True。
    # gate 已满足: P-B 修复(窗口按有效出站模型解析) + 第1/2期单测全绿 + 小窗口长
    # 会话真机 case ② 通过(24×microcompact+2×完整摘要后桌宠仍记得任务,任务连续性
    # 保住)。compaction 级联(microcompact→结构化摘要→截断兜底)对长 agentic 任务
    # 平滑续跑、防 BLOCK gate 中断。可设 [features] compaction_enabled = false 关闭。
    # 已知 caveat: haiku 摘要层偶发反射(把元指令当任务,issue #46602 式),microcompact
    # (最高频、不调模型层)无此问题;后续可继续强化防反射。
    compaction_enabled: bool = True
    # WI-1B-2 压缩可观测 (plans/.../ ctx-observability):
    # 默认 OFF = 字节级 BC。OFF 时压缩成功路径不 emit metrics、不 yield
    # ContextCompactedEvent、不发 ws context_compacted；现有 logger.info
    # ("context_compacted", ...) 全部不变。ON 时压缩命中额外:① metrics_sink
    # record 一条 "context_compacted" 事件(ratio/model);② agent_loop yield
    # ContextCompactedEvent;③ main.py 转一条 ws → 前端浮 toast「已压缩,省 N token」。
    ctx_observability: bool = True         # 测试阶段出厂点亮
    # WI-1B-3 自适应 compact_at_pct (plans/.../ compaction 三件套):
    # 默认 OFF = 字节级 BC。OFF 时 ContextConfig.compact_at_tokens_for() 直接
    # 返回 compact_at_tokens 属性,agent_loop 走原触发线。ON 时按本 run 是否
    # agentic(工具调用计数≥阈值)微调触发线: agentic 提前压(留 buffer)、纯对话
    # 延后压(少打断闲聊),结果 clamp 在 [0.6,0.95]×window。
    adaptive_compact_pct: bool = True      # 测试阶段出厂点亮
    # WI-1B-4 摘要质量回路 (同上):
    # 默认 OFF = 字节级 BC。OFF 时 _run_chat 用户消息预处理不加任何分支。ON 时
    # 对用户消息跑词法匹配(刚才|之前说的|你忘了|我们在弄|上一个) + 本 session
    # 发生过压缩(L1 有任务态快照) → 命中则从 L1 重新注入一条任务态 system 提示
    # (不立刻重摘,只补回被摘掉的任务连续性)。
    summary_quality_loop: bool = True      # 测试阶段出厂点亮
    # WI-1B-5 microcompact size-aware (同上):
    # 默认 OFF = 字节级 BC(microcompact 仍按"保护最近 N 条")。ON 时保护策略改
    # "最近 N 条 + 累计字节 ≤ M",避免最近 N 条里混入巨型 tool_result 仍撑爆窗口。
    microcompact_size_aware: bool = True   # 测试阶段出厂点亮
    # 七步问题处理流水线（plans/2026-06-24-problem-handling-pipeline-maoxuan/）。
    # 嵌套子表，由 load_config 手动 pop 构建（同 [tools.verifier] 模式）。
    problem_pipeline: ProblemPipelineConfig = field(default_factory=ProblemPipelineConfig)


@dataclass
class AppConfig:
    backend: BackendConfig = field(default_factory=BackendConfig)
    llm: LLMRoutingConfig = field(default_factory=LLMRoutingConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    vad: VADConfig = field(default_factory=VADConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    billing: BillingConfig = field(default_factory=BillingConfig)
    # 工具调用 last-mile 升级（plans/2026-05-23-tool-last-mile-upgrade/）。
    # 全 flag 默认 OFF；OFF 状态与现状字节级一致（PRD §3 G5 + TG-12）。
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    # Companion + Code 升级 v1 — slash_commands / goal_mode / agent_parallel.
    # 全 flag 默认 OFF（详 FeaturesConfig docstring）.
    features: FeaturesConfig = field(default_factory=FeaturesConfig)
    # WI-4.1 Skills 分级披露.  全 flag 默认 OFF（字节级 BC）.
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    # P4-S15: capture the raw TOML so layers that don't have a dataclass
    # yet (P4 [mcp], [agent], [context.assembler], [memory.l3], [tools.web])
    # can read their config without us having to migrate all of them at once.
    # Always a dict — empty when no config.toml exists. Treat as read-only.
    raw: dict = field(default_factory=dict)

# P4-S15: 这些 key 是 P4 通过 ``AppConfig.raw`` 读取的（[memory.l1] / [memory.l3]
# 等），不属于旧 dataclass 的 schema，但**有意保留在 config.toml 里**。
# 把它们登记下来 → _load_section 不再为它们打 warning，启动日志保持安静。
# 真正的"用户拼错 key"仍然会触发 warning。
_KNOWN_EXTRAS_BY_DATACLASS: dict[str, frozenset[str]] = {
    # MemoryConfig — 旧 dataclass 只有 db_path / embedding_model；
    # P4 三层记忆的子段都通过 AppConfig.raw["memory"] 直读。
    "MemoryConfig": frozenset({"l1", "l2", "l3", "rrf"}),
    # LLMRoutingConfig — P4-S6 引入的多 provider 段也走 raw 读。
    # P4-S20-LLM-Unified: 顶层 [llm] 直配 endpoint 字段，dataclass 没跟进
    # 但这些键由 config.raw["llm"][...] 兜底使用，不该每次启动都 warn。
    "LLMRoutingConfig": frozenset({
        "providers", "fallback_chain",
        "model", "base_url", "api_key", "temperature", "max_tokens",
        "endpoints",
        # 2026-05-17 deepseek-inline-cot-dsml-sanitize Strangler-Fig flag,
        # read via config.raw["llm"]["sanitize_inline_cot_dsml"].
        "sanitize_inline_cot_dsml",
    }),
}


def _load_section(cls, raw_dict: dict):
    """Build a dataclass from a raw dict, dropping keys the dataclass no
    longer declares.

    Rationale: a removed/renamed field in a future release shouldn't lock
    out users whose config.toml still carries the old key. Dataclass
    defaults already cover missing keys; this helper covers extra ones.

    P4-S15: P4 段（如 ``[memory.l1]`` / ``[llm.providers]``）是有意保留的
    "已知额外字段"，不应每次启动都打 warning —— 它们由 ``AppConfig.raw``
    兜底读取。
    """
    known = {f.name for f in dc_fields(cls)}
    extras_allowed = _KNOWN_EXTRAS_BY_DATACLASS.get(cls.__name__, frozenset())
    unknown = set(raw_dict) - known - extras_allowed
    if unknown:
        logger.warning(
            "config section %s ignoring unknown keys: %s",
            cls.__name__, sorted(unknown),
        )
    return cls(**{k: v for k, v in raw_dict.items() if k in known})


# ─── tools last-mile 加载 + invariant 校验 ─────────────────────
#
# 详情见 plans/2026-05-23-tool-last-mile-upgrade/00-PRD.md §3 D10 表。
# Stage 0 WI-T0.3 落地。

_VALID_VERIFY_GATE_MODES: frozenset[str] = frozenset({"off", "shadow", "strict"})

# 已注册 ephemeral subagent 模型白名单。运行时 llm_registry 才能 full lookup，
# 启动期用保守白名单挡明显错的 (TG-1 T1-11)。后续 WI-T2.4b 可在 registry
# 构造后调 register_ephemeral_model() 动态扩。
_KNOWN_EPHEMERAL_MODELS: frozenset[str] = frozenset({
    "haiku", "sonnet", "opus",
    "claude-haiku", "claude-sonnet", "claude-opus",
})


def _load_tools(raw_tools: dict) -> ToolsConfig:
    """Build ToolsConfig from raw ``[tools]`` dict.

    ``[tools.last_mile]`` / ``[tools.verifier]`` 是子表；_load_section 平铺
    解析，故 pop 出子表单独构建后装回（同 _load_memory_v2 模式）。
    """
    raw = dict(raw_tools)
    raw_lm = dict(raw.pop("last_mile", {}) or {})
    raw_v = dict(raw.pop("verifier", {}) or {})
    raw.pop("web", None)  # [tools.web] 由 tools/_config.py 单独加载，非 ToolsConfig 字段
    last_mile = _load_section(ToolsLastMileConfig, raw_lm)
    verifier = _load_section(ToolsVerifierConfig, raw_v)
    # WI-T5.1 v3: 顶层 [tools] 字段（disabled_toolsets 等）此前被漏读 → 配了不生效。
    # 显式补读，使 config.toml 的 toolset 门控 / dangerous 白名单 / 默认超时真正生效。
    return ToolsConfig(
        last_mile=last_mile,
        verifier=verifier,
        disabled_toolsets=list(raw.get("disabled_toolsets", []) or []),
        disabled_toolsets_schema_only=list(raw.get("disabled_toolsets_schema_only", []) or []),
        dangerous_tools_allowlist=list(raw.get("dangerous_tools_allowlist", []) or []),
        default_timeout_seconds=float(raw.get("default_timeout_seconds", 60.0) or 60.0),
        strict_unknown_toolset=bool(raw.get("strict_unknown_toolset", False)),
    )


def _validate_flag_invariants(cfg: AppConfig) -> None:
    """PRD §3 D10 flag 组合 invariant 启动期校验。

    冲突分两类：
      - **致命**：raise ConfigError（拒启动），错误码 VG-INVARIANT-{0,1,5,6}。
      - **软**：warn log + 就地修正字段（让用户能继续启动，但日志提示）。

    被 load_config 在 return 前调用；测试见 TG-1 T1-3/T1-7~T1-11。
    """
    lm = cfg.tools.last_mile
    v = cfg.tools.verifier

    # VG-INVARIANT-0: verify_gate_mode 取值合法
    if v.verify_gate_mode not in _VALID_VERIFY_GATE_MODES:
        raise ConfigError(
            f"VG-INVARIANT-0: verify_gate_mode={v.verify_gate_mode!r} "
            f"must be one of {sorted(_VALID_VERIFY_GATE_MODES)}"
        )

    # VG-INVARIANT-1: verify_gate_mode != off 必须 emit_receipts=true
    if v.verify_gate_mode != "off" and not v.emit_receipts:
        raise ConfigError(
            f"VG-INVARIANT-1: verify_gate_mode={v.verify_gate_mode!r} "
            f"requires emit_receipts=true, otherwise ledger is always empty "
            f"and end_turn is always blocked."
        )

    # VG-INVARIANT-6: artifact_dir_retention_days 1..365
    days = lm.artifact_dir_retention_days
    if days < 1 or days > 365:
        raise ConfigError(
            f"VG-INVARIANT-6: artifact_dir_retention_days={days} "
            f"out of range [1, 365]"
        )

    # 软冲突：run_build=true 且 verify_gate_mode=off → 自动转 shadow
    if v.run_build and v.verify_gate_mode == "off":
        logger.warning(
            "config [tools.verifier]: run_build=true with "
            "verify_gate_mode='off' is incoherent; auto-promoting "
            "verify_gate_mode='shadow'."
        )
        v.verify_gate_mode = "shadow"

    # 软冲突：frontend_artifact_card=true 但 artifact_envelope=false
    if lm.frontend_artifact_card and not lm.artifact_envelope:
        logger.warning(
            "config [tools.last_mile]: frontend_artifact_card=true requires "
            "artifact_envelope=true; auto-disabling frontend_artifact_card."
        )
        lm.frontend_artifact_card = False

    # 软冲突：tauri_artifact_ops=true 但 frontend_artifact_card=false (允许)
    if lm.tauri_artifact_ops and not lm.frontend_artifact_card:
        logger.warning(
            "config [tools.last_mile]: tauri_artifact_ops=true with "
            "frontend_artifact_card=false; Rust commands reachable but no "
            "UI button will invoke them (test scenario only)."
        )

    # VG-INVARIANT-5 (N5): ephemeral_subagent_model 白名单 / 默认填充
    if v.verify_gate_mode != "off":
        m = (v.ephemeral_subagent_model or "").strip()
        if not m:
            logger.warning(
                "config [tools.verifier]: ephemeral_subagent_model is empty "
                "with verify_gate_mode != 'off'; defaulting to 'haiku'."
            )
            v.ephemeral_subagent_model = "haiku"
        elif m not in _KNOWN_EPHEMERAL_MODELS and not m.startswith("claude-"):
            raise ConfigError(
                f"VG-INVARIANT-5: unknown ephemeral_subagent_model={m!r}; "
                f"expected one of {sorted(_KNOWN_EPHEMERAL_MODELS)} or a "
                f"'claude-*' model id."
            )


def _load_memory_v2(raw_v2: dict) -> MemoryV2Config:
    """Build MemoryV2Config from the raw ``[memory.v2]`` dict.

    ``_load_section`` is flat (no nested-dataclass recursion), so the
    ``[memory.v2.facts]`` / ``[memory.v2.forget]`` sub-sub-tables are
    popped + built separately. Missing section / keys → all flags
    default False (第一代行为不变)。
    """
    raw_facts = dict(raw_v2.pop("facts", {}) or {})
    raw_forget = dict(raw_v2.pop("forget", {}) or {})
    facts = _load_section(MemoryV2FactsConfig, raw_facts)
    forget = _load_section(MemoryV2ForgetConfig, raw_forget)
    v2 = _load_section(MemoryV2Config, raw_v2)
    v2.facts = facts
    v2.forget = forget
    return v2


def _bundle_default_config_path() -> Path | None:
    """Return the bundle's default config.toml (seed source), or None if missing.

    * Frozen (PyInstaller): ``_MEIPASS/config.toml`` (added in P4-S21 to
      the spec's ``datas``) → ``<exe_dir>/config.toml`` (Tauri may also
      drop one as resource) → upward search for repo-style layouts.
    * Dev: ``<repo>/config.toml`` — ``backend/../config.toml``.
    """
    if getattr(sys, "frozen", False):
        # P4-S21 #12: prefer _MEIPASS — that's where the spec ships
        # `../config.toml` to. Without this, frozen builds with no
        # `<exe_dir>/config.toml` were returning None and
        # seed_user_config_if_missing() couldn't migrate legacy schemas
        # because the migration source itself was missing.
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            c = Path(meipass) / "config.toml"
            if c.is_file():
                return c
        exe_dir = Path(sys.executable).resolve().parent
        for up in (0, 1, 2, 3):
            candidate = (exe_dir.parents[up - 1] if up else exe_dir) / "config.toml"
            if candidate.is_file():
                return candidate
        return None
    dev = Path(__file__).resolve().parent.parent / "config.toml"
    return dev if dev.is_file() else None


def _is_legacy_llm_schema(raw: dict) -> bool:
    """Detect pre-P4-S20-LLM-Unified config layout.

    Old format had ``[llm.local]`` and/or ``[llm.cloud]`` subtables under
    ``[llm]``; new format is a flat ``[llm]`` with model/base_url/api_key
    directly. The crash source for users upgrading from old MSIs was the
    cloud provider trying to hit ``vcrppsmofoyv.cloud.sealos.io`` even
    though the user never configured it — that URL is hardcoded in the
    legacy ``[llm.cloud].base_url``.
    """
    llm = raw.get("llm")
    if not isinstance(llm, dict):
        return False
    return ("local" in llm and isinstance(llm["local"], dict)) or (
        "cloud" in llm and isinstance(llm["cloud"], dict)
    )


# ─── factory feature-flag backfill (additive schema migration) ──────────
#
# Gap fixed 2026-06-23 (follow-up to WI-OH-4 curation 死链, commit b8d57bf3):
# `seed_user_config_if_missing` only ever (a) seeds a full config on first
# run or (b) wholesale-replaces a *legacy* `[llm.local]/[llm.cloud]` config.
# An existing **unified** user config was never touched — so any new factory
# flag added to the bundle `config.toml` (e.g. `[memory.v2] curation_nudge /
# auto_learnings`, historically `facts_extract`) only reached *fresh*
# installs; 存量用户 kept their old `%APPDATA%\deskpet\config.toml` forever
# and the feature stayed dark at runtime (resolve_config_path() prefers that
# AppData file).
#
# `_merge_missing_feature_flags` closes that gap: on every startup it
# additively backfills **only** the allow-listed feature-flag keys that the
# bundle default defines but the user is missing. User-customised values and
# comments are preserved byte-for-byte; nothing is ever overwritten.

# Allow-listed TOML table paths whose *missing* keys are safe to backfill.
# These are behaviour-flag / capability-gate / tuning sections (Strangler-Fig
# switches). DELIBERATELY EXCLUDED — and why:
#   * [llm] / [llm.providers] / [llm.fallback_chain] / [llm_p4*] — endpoint +
#     model + api_key. The user's real runtime values live in llm_runtime.json
#     (Settings panel writes there), and api_key MUST NEVER be written into
#     config.toml (commit would leak it). Backfilling could also re-introduce
#     a stale default model.
#   * [backend] — host/port the user may have changed.
#   * [billing] / [billing.pricing] — user spend caps / private pricing.
#   * [asr] / [tts] / [vad] / [voice] — device + voice preferences.
#   * [memory] (db_path) — a user filesystem path.
#   * [[mcp.servers]] — array-of-tables the user may have customised; an
#     additive *scalar* merge can't model array-of-tables safely.
_MIGRATABLE_SECTIONS: tuple[tuple[str, ...], ...] = (
    ("tools", "last_mile"),
    ("tools", "verifier"),
    ("supervisor",),
    ("companion",),
    ("image",),
    ("memory", "v2"),
    ("memory", "v2", "facts"),
    ("memory", "v2", "forget"),
    ("context", "manager"),
    ("context", "assembler"),
    ("code_e2e",),
    ("research",),
    # 2026-06-27 全量点亮：[features]/[skills] 新默认须能回灌进存量用户 config。
    ("features",),
    ("skills",),
    ("skills", "auto_disclosure"),
    ("skills", "codify"),
)


def _is_toml_table(obj: object) -> bool:
    """True for a tomlkit table / inline-table (mapping-like) node.

    Duck-typed (``keys`` + ``__getitem__``) so we don't import tomlkit's
    internal item types — scalars (int/str/bool) and arrays lack ``keys``.
    """
    return hasattr(obj, "keys") and hasattr(obj, "__getitem__") and not isinstance(
        obj, (str, bytes)
    )


def _dig_table(doc: object, path: tuple[str, ...]) -> object | None:
    """Walk ``path`` through nested tomlkit tables. Returns the node or None
    if any segment is absent / not a table."""
    cur: object = doc
    for seg in path:
        if not _is_toml_table(cur):
            return None
        try:
            if seg not in cur:  # type: ignore[operator]
                return None
            cur = cur[seg]  # type: ignore[index]
        except (TypeError, KeyError):
            return None
    return cur


def _merge_missing_feature_flags(user_target: Path, bundle_source: Path) -> bool:
    """Additively backfill allow-listed factory feature-flag keys that the
    bundle default defines but the user's config is missing.

    Non-destructive: existing user keys/values/comments are preserved; only
    *missing* keys (and whole missing allow-listed sections) are inserted,
    carrying the bundle's inline trivia. Writes a ``.pre-migrate-bak`` backup
    before saving. Idempotent — a second run finds nothing missing.

    Requires tomlkit (comment-preserving writer). If tomlkit is unavailable,
    or parsing/writing fails, logs a warning and returns False — it NEVER
    raises, so a backfill problem can't block backend startup.

    Returns True iff the file was modified.
    """
    try:
        import copy

        import tomlkit
    except Exception as e:  # pragma: no cover - import guard
        logger.warning("feature_flag_merge_skipped (no tomlkit): %s", e)
        return False

    try:
        user_doc = tomlkit.parse(user_target.read_text(encoding="utf-8"))
        bundle_doc = tomlkit.parse(bundle_source.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("feature_flag_merge_parse_failed: %s", e)
        return False

    added: list[str] = []
    for path in _MIGRATABLE_SECTIONS:
        bundle_tbl = _dig_table(bundle_doc, path)
        if not _is_toml_table(bundle_tbl):
            continue  # bundle doesn't define this section — nothing to seed
        user_tbl = _dig_table(user_doc, path)
        if user_tbl is None:
            # Whole section missing → copy it wholesale (preserves comments).
            parent = _dig_table(user_doc, path[:-1])
            if not _is_toml_table(parent):
                continue  # parent absent & not separately allow-listed — skip
            try:
                parent[path[-1]] = copy.deepcopy(bundle_tbl)  # type: ignore[index]
            except Exception as e:
                logger.warning("feature_flag_merge_section_copy_failed %s: %s", path, e)
                continue
            added.append(".".join(path) + " (section)")
            continue
        for key in list(bundle_tbl.keys()):  # type: ignore[union-attr]
            if key in user_tbl:  # type: ignore[operator]
                continue
            bval = bundle_tbl[key]  # type: ignore[index]
            if _is_toml_table(bval):
                continue  # nested sub-table handled via its own allowlist entry
            try:
                user_tbl[key] = copy.deepcopy(bval)  # type: ignore[index]
            except Exception as e:
                logger.warning(
                    "feature_flag_merge_key_copy_failed %s.%s: %s", path, key, e
                )
                continue
            added.append(".".join(path) + "." + str(key))

    if not added:
        return False

    try:
        bak = user_target.with_suffix(".pre-migrate-bak")
        shutil.copyfile(user_target, bak)
        user_target.write_text(tomlkit.dumps(user_doc), encoding="utf-8")
        logger.info(
            "feature_flag_merge_applied count=%d keys=%s backup=%s",
            len(added), added, bak,
        )
        return True
    except OSError as e:
        logger.warning("feature_flag_merge_write_failed: %s", e)
        return False


def seed_user_config_if_missing() -> Path | None:
    """First-run: copy the bundle's config.toml into user_data_dir if the
    user doesn't have one yet. Also: P4-S21 #12 — if user_data_dir/config.toml
    exists but uses the legacy ``[llm.local]/[llm.cloud]`` schema, back it up
    (``.legacy-bak``) and replace with the bundle default. Returns the user
    path on success, or None if seeding/migration both failed (caller then
    falls through to bundle / AppConfig defaults).
    """
    user_target = _paths.user_data_dir() / "config.toml"

    # P4-S21 #12: legacy-schema migration. Runs once per upgrade — after
    # the swap, the file no longer matches the legacy detector so we
    # never touch it again. Existing llm_runtime.json (the user's actual
    # base_url/model/api_key) is intentionally NOT touched: those are
    # the runtime overrides applied on top of config.toml at load time.
    if user_target.is_file():
        try:
            with open(user_target, "rb") as f:
                raw = tomli.load(f)
        except Exception as e:
            logger.warning("legacy_check_parse_failed: %s", e)
            return user_target
        if _is_legacy_llm_schema(raw):
            source = _bundle_default_config_path()
            if source is None:
                logger.warning(
                    "legacy_llm_schema_detected_but_no_bundle_source",
                )
                return user_target
            try:
                bak = user_target.with_suffix(".legacy-bak")
                shutil.copyfile(user_target, bak)
                shutil.copyfile(source, user_target)
                logger.info(
                    "legacy_llm_schema_migrated source=%s target=%s backup=%s",
                    source, user_target, bak,
                )
            except OSError as e:
                logger.warning(
                    "legacy_llm_schema_migrate_failed: %s", e,
                )
            return user_target

        # Non-legacy existing config → additively backfill any new factory
        # feature-flag keys the user is missing (the 2026-06-23 gap). Fully
        # self-guarded; the extra try/except is belt-and-suspenders so a
        # backfill problem can never break startup.
        source = _bundle_default_config_path()
        if source is not None and source.is_file():
            try:
                _merge_missing_feature_flags(user_target, source)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("feature_flag_merge_unexpected: %s", e)
        return user_target

    source = _bundle_default_config_path()
    if source is None:
        return None
    try:
        user_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, user_target)
        logger.info(
            "seeded user config.toml from bundle: %s -> %s", source, user_target,
        )
        return user_target
    except OSError as e:
        logger.warning("config seed failed (%s); falling back to bundle default", e)
        return None


def get_subagent_concurrency(cfg: "AppConfig") -> tuple[int, dict[str, int]]:
    """子代理并发驱动：从 ``cfg.raw['agent']['concurrency']`` 读全局/各 lane cap。

    plan WI-0.3。**必须 getattr 兜底**（cfg 可能是无 ``.raw`` 的测试 stub）——
    不复刻 2026-06-21 `b05823b` 的 ``config.config`` 单例不存在导致配置静默
    失效的坑（R2）。缺省 → 内置默认 ``(4, {...})``。
    """
    raw = (getattr(cfg, "raw", None) or {})
    agent_raw = raw.get("agent", {}) if isinstance(raw, dict) else {}
    conc = agent_raw.get("concurrency", {}) if isinstance(agent_raw, dict) else {}
    if not isinstance(conc, dict):
        conc = {}
    try:
        glob = int(conc.get("global_concurrency", 4))
    except (TypeError, ValueError):
        glob = 4
    lanes_raw = conc.get("lane_caps", {})
    default_lanes = {
        "research": 2,
        "code": 2,
        "fileops": 3,
        "doc": 1,
        "web": 3,
        "general": 2,
    }
    lanes: dict[str, int] = dict(default_lanes)
    if isinstance(lanes_raw, dict):
        for k, v in lanes_raw.items():
            try:
                lanes[str(k)] = int(v)
            except (TypeError, ValueError):
                continue
    return glob, lanes


def resolve_config_path() -> Path:
    """Return the config.toml path the backend should load, with priority:

    1. ``DESKPET_CONFIG`` env (E2E / tests / power users).
    2. ``<user_data_dir>/config.toml`` — seeded on first run from the bundle.
    3. Bundle default (frozen exe dir or dev repo root).
    4. Whatever fallback we can find, even if missing — ``load_config`` will
       silently return ``AppConfig()`` defaults in that case.
    """
    override = os.environ.get("DESKPET_CONFIG")
    if override:
        p = Path(override)
        if p.is_file():
            return p
        logger.warning("DESKPET_CONFIG=%s does not exist; falling through", override)

    # Try (or create) the user-data copy.
    seeded = seed_user_config_if_missing()
    if seeded is not None and seeded.is_file():
        return seeded

    # Fall through: bundle default.
    bundle = _bundle_default_config_path()
    if bundle is not None:
        return bundle

    # Last resort: a path that doesn't exist. load_config() returns
    # AppConfig() defaults when the path is missing, which is the
    # correct behaviour — we just won't read anything from disk.
    return _paths.user_data_dir() / "config.toml"


def effective_llm_model(cfg: "AppConfig") -> str:
    """有效出站 LLM 模型名。优先 dataclass(运行时覆盖已应用) → raw → 种子默认。
    cfg 可能是部分构造/None-字段，全程空安全，绝不抛。
    """
    try:
        model = cfg.llm.local.model
        if isinstance(model, str) and model:
            return model
    except Exception:
        pass

    try:
        raw = cfg.raw or {}
        if isinstance(raw, dict):
            llm = raw.get("llm", {}) or {}
            if isinstance(llm, dict):
                model = llm.get("model")
                if isinstance(model, str) and model:
                    return model
    except Exception:
        pass

    return "gemma4:e4b"


def effective_llm_model_standalone() -> str:
    """工具进程内取有效出站模型，不依赖 main 的 config 单例(那个单例工具拿不到)。
    优先直读 <user_data>/llm_runtime.json 的 model → 回落磁盘 config → 种子默认。失败静默。
    """
    try:
        rt = _paths.user_data_dir() / "llm_runtime.json"
        if rt.exists():
            data = json.loads(rt.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                model = data.get("model")
                if isinstance(model, str) and model:
                    return model
    except Exception:
        pass

    try:
        cfg = load_config(resolve_config_path())
        raw = cfg.raw or {}
        if isinstance(raw, dict):
            llm = raw.get("llm", {}) or {}
            if isinstance(llm, dict):
                model = llm.get("model")
                if isinstance(model, str) and model:
                    return model
    except Exception:
        pass

    return "gemma4:e4b"


def standalone_config_section(section: str) -> dict:
    """工具进程内读 config.toml 某段(dict),不依赖 main 的 config 单例。

    历史 bug(真机 UI 测 TC-P2-05): 各工具用
    ``import config as _cfg; _cfg.config.raw.get("<section>")`` 读配置,但
    ``config`` 模块**并无** ``config`` 属性 —— 已加载的 ``AppConfig`` 是
    ``main.py`` 的 ``main.config`` 全局,工具 import 的 ``config`` 模块拿不到。
    于是 ``_cfg.config`` 恒抛 ``AttributeError`` 被外层 except 吞掉 → 用户在
    config.toml 配的开关(``[image] model/quality/...``、``[research] ...`` 等)
    **从未生效**,恒取代码里的默认值。

    本 helper 正确解析磁盘上的 config 并返回指定段。借 :func:`load_config`
    的进程级 mtime 缓存,重复调用是廉价的(用户改 config.toml 会自动失效重读)。
    读不到 / 段不存在 / 段非 dict → 一律返回 ``{}``,全程不抛。

    See also :func:`effective_llm_model_standalone`(同样的「工具拿不到单例」
    根因,只是它针对 ``[llm].model``)。
    """
    try:
        cfg = load_config(resolve_config_path())
        raw = cfg.raw or {}
        if isinstance(raw, dict):
            val = raw.get(section)
            if isinstance(val, dict):
                return val
    except Exception:  # noqa: BLE001 — 配置缺失/损坏一律回落默认
        pass
    return {}


def _resolve_memory_db_path(raw: str) -> Path:
    """Map a MemoryConfig.db_path value to an absolute Path.

    * Empty string → ``<user_data_dir>/data/memory.db`` (new default).
    * Absolute path → used verbatim.
    * Relative path → resolved under ``<user_data_dir>/`` (so legacy
      ``"./data/memory.db"`` in an old config still works, just now
      pointing at AppData instead of CWD).
    """
    if not raw:
        return _paths.user_data_dir() / "data" / "memory.db"
    p = Path(raw)
    if p.is_absolute():
        return p
    # Legacy relative form: anchor to user_data_dir rather than CWD.
    # Strip leading "./" so the join doesn't produce "user_data/./data/..."
    rel = raw.lstrip(".").lstrip("/").lstrip("\\")
    return _paths.user_data_dir() / rel


# WI-T5.1 v3 子项：process-wide cache + mtime 失效（TDD §A12 P1-2）.
# 原 load_config 每次 import 都重读 toml — 现网无影响（main.py 顶层只调一次），
# 但 build_agent 等工厂调用 + pytest fixtures 反复 import 累计 IO 浪费。
# Cache 行为：
#   - 第一次 load_config(path) → 真读 toml + 记 mtime
#   - 后续 load_config(path) → 比对 mtime，未变即返 cached
#   - mtime 改变 / path 不同 → invalidate + 重读
#   - 文件不存在 → 不缓存（保持每次重建默认 AppConfig，方便 test fixture
#     反复 monkeypatch _resolve_memory_db_path）
#
# WI-T5.1 v3.1 — cache key 同时纳入 st_size（不只 mtime）：
# Windows 文件系统 mtime 精度有限，**同一路径在同一 mtime tick 内连续重写两次**
# （rsync/make 同款陷阱）会让 mtime-only 比对误判"未变"→ 返回 stale config。
# 这正是 test_t1_11 在 warm 进程下偶发 "DID NOT RAISE VG-INVARIANT-5" 的根因：
# 它对同一 tmp config.toml 先写空 model（合法）再写 unknown_model（应报错），
# 两次写常落同一 tick → 第二次 load_config 命中 cache 返回第一次的合法对象，
# 校验路径被旁路。size 不同即可强制失效（mtime+size 是标准稳健启发式）。
_cfg_cache: Optional["AppConfig"] = None
_cfg_cache_path: Optional[Path] = None
_cfg_cache_mtime: Optional[float] = None
_cfg_cache_size: Optional[int] = None


def _load_config_uncached(path: Path) -> "AppConfig":
    """Real loader — extracted so cache wrapper stays small."""
    return _load_config_impl(path)


def load_config(path: str | Path = "config.toml") -> AppConfig:
    """Process-wide cached AppConfig loader (WI-T5.1 v3).

    Returns the previously-built AppConfig instance when path + mtime are
    unchanged since last call. Reload happens automatically when the user
    edits config.toml (mtime bumps) or when called with a different path.
    """
    global _cfg_cache, _cfg_cache_path, _cfg_cache_mtime, _cfg_cache_size
    path = Path(path)
    if path.exists():
        try:
            st = path.stat()
            cur_mtime: Optional[float] = st.st_mtime
            cur_size: Optional[int] = st.st_size
        except OSError:
            cur_mtime = None
            cur_size = None
        if (
            _cfg_cache is not None
            and _cfg_cache_path == path
            and _cfg_cache_mtime is not None
            and cur_mtime is not None
            and abs(cur_mtime - _cfg_cache_mtime) < 1e-6
            # size 一并比对：挡 mtime 精度内同路径重写（见上方注释 / test_t1_11）
            and _cfg_cache_size is not None
            and cur_size == _cfg_cache_size
        ):
            return _cfg_cache
        cfg = _load_config_impl(path)
        _cfg_cache = cfg
        _cfg_cache_path = path
        _cfg_cache_mtime = cur_mtime
        _cfg_cache_size = cur_size
        return cfg
    # 文件不存在 — 不缓存（保留每次重建默认 AppConfig 的语义）
    return _load_config_impl(path)


def _reset_load_config_cache() -> None:
    """Test helper：清缓存（避免 fixture 间状态泄漏）。"""
    global _cfg_cache, _cfg_cache_path, _cfg_cache_mtime, _cfg_cache_size
    _cfg_cache = None
    _cfg_cache_path = None
    _cfg_cache_mtime = None
    _cfg_cache_size = None


def _load_config_impl(path: str | Path = "config.toml") -> AppConfig:
    path = Path(path)
    if not path.exists():
        # Even on a totally blank system we still want db_path resolved
        # into user_data_dir rather than whatever CWD we happened to
        # inherit. Build AppConfig() first then let the resolution
        # below rewrite memory.db_path / billing.db_path.
        config = AppConfig()
        config.memory.db_path = str(_resolve_memory_db_path(""))
        config.billing = BillingConfig.from_toml(
            {}, db_dir=Path(config.memory.db_path).parent
        )
        return config
    with open(path, "rb") as f:
        raw = tomli.load(f)
    config = AppConfig()
    if "backend" in raw:
        config.backend = _load_section(BackendConfig, raw["backend"])
    # DESKPET_BACKEND_PORT env override — lets a second checkout / git
    # worktree run its own dev backend on a different port without
    # editing config.toml. The Tauri shell (process_manager.rs) reads
    # the SAME env var so both halves of the handshake agree.
    _port_override = os.environ.get("DESKPET_BACKEND_PORT")
    if _port_override:
        try:
            config.backend.port = int(_port_override.strip())
        except ValueError:
            pass
    if "llm" in raw:
        raw_llm = raw["llm"]
        raw_local = raw_llm.pop("local", None)
        raw_cloud = raw_llm.pop("cloud", None)
        # P2-1-S3: cloud [api_key] now lives in the OS credential store
        # (Windows Credential Manager / Keychain / Secret Service) and is
        # injected as DESKPET_CLOUD_API_KEY env by the Tauri wrapper. A
        # plaintext value sitting in config.toml is a migration leftover
        # we want to nudge the user about. Placeholder "sk-..." stays
        # quiet — the default config ships with that value.
        if raw_cloud is not None:
            leaked = (raw_cloud.get("api_key") or "").strip()
            if leaked and leaked not in {"sk-...", "your-key-here"}:
                logger.warning(
                    "config [llm.cloud].api_key is plaintext — IGNORED for "
                    "provider init. Cloud API key now lives in the OS keyring "
                    "(set via SettingsPanel → 云端账号). Remove this line "
                    "from config.toml once migrated. (P2-1-S3)"
                )
        # P4-S20-LLM-Unified: pre-P2-1-S2 schema 警告 deprecated —
        # 新统一格式正好用这些 key (model/base_url/api_key/...) 在 [llm] 段下，
        # 旧 deprecation warning 反而会误报。新 schema 下面会明确 promote 这些
        # 字段到 routing.local。
        routing = _load_section(LLMRoutingConfig, raw_llm)
        if raw_local is not None:
            routing.local = _load_section(LLMEndpointConfig, raw_local)
        if raw_cloud is not None:
            routing.cloud = _load_section(LLMEndpointConfig, raw_cloud)

        # P4-S20-LLM-Unified: 新统一格式 — [llm] 段直接含 endpoint 字段
        # (model/base_url/api_key/temperature/max_tokens)，没有 [llm.local]
        # 也没有 [llm.cloud]。此处是关键迁移点：如果用户的 [llm] 段直接
        # 含 endpoint 字段（且 [llm.local] 缺失），把这些字段抽出来填到
        # routing.local。这样下游所有读 routing.local 的代码无感升级。
        _ENDPOINT_KEYS = {
            "model", "base_url", "api_key", "temperature", "max_tokens"
        }
        new_format_keys = _ENDPOINT_KEYS & set(raw_llm)
        if new_format_keys and raw_local is None:
            # User is on the new unified schema → promote to local slot.
            endpoint_kwargs: dict = {}
            for k in new_format_keys:
                endpoint_kwargs[k] = raw_llm[k]
            try:
                routing.local = LLMEndpointConfig(**endpoint_kwargs)
                logger.info(
                    "llm_unified_schema_loaded fields=%s base_url=%s model=%s",
                    sorted(new_format_keys),
                    routing.local.base_url,
                    routing.local.model,
                )
            except TypeError as exc:
                logger.warning(
                    "llm_unified_schema_invalid: %s", exc,
                )
        config.llm = routing
    if "asr" in raw:
        config.asr = _load_section(ASRConfig, raw["asr"])
    if "tts" in raw:
        config.tts = _load_section(TTSConfig, raw["tts"])
        # P3-S1: strip legacy './assets/...' / 'assets/...' / './' prefixes
        # so everything downstream is a bare subfolder name paths.resolve_model_dir
        # can join onto model_root(). Loud WARNING nudges users to update
        # their config.toml.
        legacy_prefixes = ("./assets/", "assets/", "./")  # p3-s1-allow-assets: legacy migration
        original = config.tts.model_dir
        if original.startswith(legacy_prefixes):
            stripped = original
            for prefix in legacy_prefixes:
                if stripped.startswith(prefix):
                    stripped = stripped[len(prefix):]
                    break
            # Collapse any accidental nested './' remainders.
            stripped = stripped.lstrip("./")
            logger.warning(
                "config [tts].model_dir uses legacy relative path %r; "
                "normalizing to bare subfolder %r (P3-S1). Please update "
                "config.toml to avoid this warning.",
                original, stripped,
            )
            config.tts.model_dir = stripped
    if "vad" in raw:
        config.vad = _load_section(VADConfig, raw["vad"])
    if "voice" in raw:
        config.voice = _load_section(VoiceConfig, raw["voice"])
    # Companion + Code 升级 v1 功能开关 [features] — 此前 load_config 漏读该段
    # (和 _load_tools 漏读 disabled_toolsets 同类 bug), 导致 slash_commands /
    # goal_mode / agent_parallel 在 config.toml 配了也不生效(config.features
    # 永远是默认全 False)。补读使这三个功能能真正开启。
    if "features" in raw:
        # [features.problem_pipeline] 是嵌套子表（同 [memory.v2] / [tools.verifier]）
        # —— _load_section 只做平铺解析会丢弃它，必须先 pop 出来单独构建（plans/2026-06-24-...）。
        raw_features = dict(raw["features"])
        raw_pp = dict(raw_features.pop("problem_pipeline", {}) or {})
        config.features = _load_section(FeaturesConfig, raw_features)
        config.features.problem_pipeline = _load_section(ProblemPipelineConfig, raw_pp)
    if "memory" in raw:
        # [memory.v2] / [memory.v2.facts] 是嵌套子表，_load_section 只做
        # 平铺解析 —— 先把 v2 pop 出来单独构建，再装回。
        raw_mem = dict(raw["memory"])
        raw_v2 = dict(raw_mem.pop("v2", {}) or {})
        config.memory = _load_section(MemoryConfig, raw_mem)
        config.memory.v2 = _load_memory_v2(raw_v2)
    # P3-S7: always funnel memory.db_path through the AppData resolver, so
    # empty/relative values become absolute user_data_dir paths and absolute
    # ones pass through. This also catches the AppConfig() defaults when
    # no [memory] section is present.
    resolved_mem = _resolve_memory_db_path(config.memory.db_path)
    config.memory.db_path = str(resolved_mem)
    # BillingConfig always resolved — even if [billing] is absent we want a
    # default daily_budget_cny so main.py can construct the ledger. Pin it
    # to the same directory as memory.db so the two SQLite files stay together.
    db_dir = resolved_mem.parent
    config.billing = BillingConfig.from_toml(raw, db_dir=db_dir)
    # 工具调用 last-mile 升级 — [tools.last_mile] / [tools.verifier] 嵌套
    # 子表（同 [memory.v2] 模式：_load_section 平铺，子表单独 dispatch）。
    if "tools" in raw:
        config.tools = _load_tools(raw["tools"])
    # Companion+Code v1/v2 — [features] flat dataclass 解析。
    # 包含 slash_commands / goal_mode / agent_parallel 3 flag（默认 OFF）.
    if "features" in raw:
        # [features.problem_pipeline] 嵌套子表幂等 pop 构建（同上方第一处；两处都需，勿盲删，
        # 见 plans/2026-06-24-... §M4 红队 m-3）。
        raw_features = dict(raw["features"])
        raw_pp = dict(raw_features.pop("problem_pipeline", {}) or {})
        config.features = _load_section(FeaturesConfig, raw_features)
        config.features.problem_pipeline = _load_section(ProblemPipelineConfig, raw_pp)
    # WI-4.1 skills 分级披露 + WI-4.3 技能自创配置加载（[skills.auto_disclosure]
    # / [skills.codify] 子表）.
    # 2026-06-06 真机手测抓 bug：原加载器只 pop auto_disclosure，**从不解析 codify**
    # → `[skills.codify] enabled=true` 被丢弃 → config.skills.codify 恒默认(enabled=
    # False) → lifespan codify 接线跳过 → tool_path_recorder/skill_candidate_store
    # 全 None → 技能自创确认卡生产永不弹（boot 无 fp5_codify_wiring_ready 印证）。
    if "skills" in raw:
        raw_skills = dict(raw["skills"])
        knowledge_enabled = bool(raw_skills.pop("knowledge_enabled", False))
        raw_ad = dict(raw_skills.pop("auto_disclosure", {}) or {})
        ad = _load_section(SkillsAutoDisclosureConfig, raw_ad)
        raw_cd = dict(raw_skills.pop("codify", {}) or {})
        cd = _load_section(SkillsCodifyConfig, raw_cd)
        config.skills = SkillsConfig(
            knowledge_enabled=knowledge_enabled,
            auto_disclosure=ad,
            codify=cd,
        )
    # P4-S15: stash the raw parsed TOML so consumers (MCP bootstrap, agent
    # bootstrap, etc.) can pick out their sections without us bolting on
    # a dataclass for each one.
    config.raw = dict(raw)
    # 启动期 invariant 校验（PRD §3 D10）：致命错 raise ConfigError，
    # 软冲突 warn log + 就地修正。在 return 前调，确保 config.raw 也已就位。
    _validate_flag_invariants(config)
    return config
