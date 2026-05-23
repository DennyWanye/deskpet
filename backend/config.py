from __future__ import annotations
import logging
import os
import shutil
import sys
import tomli
from pathlib import Path
from dataclasses import dataclass, field, fields as dc_fields

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
    feedback_loop: bool = False       # WI-M1.1 用户 thumbs-up 回路
    facts_extract: bool = False       # WI-M1.2 写入端事实抽取
    rerank: bool = False              # WI-M1.3 cross-encoder 重排
    enhanced_retriever: bool = False  # WI-M1.4 facts 进 RRF
    chunking: bool = False            # WI-M1.5 长消息切块
    query_rewrite: bool = False       # WI-M1.5 短查询改写
    workspace_memory: bool = False    # WI-M1.6 code 工作记忆
    reflection: bool = False          # WI-M1.7 反思 / skill memory
    # Stage 2 新增 4 flag（PRD §2.1 G1-G5 / §3 D16）：
    cross_key_merge: bool = False         # WI-S2.1a 跨 key 矛盾治理
    memory_forget: bool = False           # WI-S2.1a 显式遗忘工具
    entity_path: bool = False             # WI-S2.2 entity 索引检索路
    episodic_to_semantic: bool = False    # WI-S2.4 summary 抽 facts
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
    emit_receipts: bool = False
    verify_gate_mode: str = "off"                  # off | shadow | strict
    extractor_fallback_enabled: bool = True        # D6 二级 LLM fallback
    ephemeral_subagent_model: str = "haiku"        # D6 第 3 次失败救援模型
    run_build: bool = False                        # D7 build verifier
    run_tests: bool = False                        # D7 test verifier
    claim_patterns_file: str = "verify/claim_patterns.yaml"


@dataclass
class ToolsConfig:
    """``[tools]`` 父表，含 last_mile / verifier 两个子表。

    _load_section 平铺解析，无法直接处理嵌套子 dataclass —— load_config 把
    子表 pop 出来单独构建（_load_tools，同 _load_memory_v2 模式）。
    """
    last_mile: ToolsLastMileConfig = field(default_factory=ToolsLastMileConfig)
    verifier: ToolsVerifierConfig = field(default_factory=ToolsVerifierConfig)


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
    last_mile = _load_section(ToolsLastMileConfig, raw_lm)
    verifier = _load_section(ToolsVerifierConfig, raw_v)
    return ToolsConfig(last_mile=last_mile, verifier=verifier)


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


def load_config(path: str | Path = "config.toml") -> AppConfig:
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
    # P4-S15: stash the raw parsed TOML so consumers (MCP bootstrap, agent
    # bootstrap, etc.) can pick out their sections without us bolting on
    # a dataclass for each one.
    config.raw = dict(raw)
    # 启动期 invariant 校验（PRD §3 D10）：致命错 raise ConfigError，
    # 软冲突 warn log + 就地修正。在 return 前调，确保 config.raw 也已就位。
    _validate_flag_invariants(config)
    return config
