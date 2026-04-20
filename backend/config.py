from __future__ import annotations
import logging
import os
import tomli
from pathlib import Path
from dataclasses import dataclass, field, fields as dc_fields

logger = logging.getLogger(__name__)


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

@dataclass
class TTSConfig:
    provider: str = "edge-tts"
    voice: str = "zh-CN-XiaoyiNeural"
    model_dir: str = "./assets/cosyvoice2"

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
class MemoryConfig:
    db_path: str = "./data/memory.db"
    embedding_model: str = "bge-m3"


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

def _load_section(cls, raw_dict: dict):
    """Build a dataclass from a raw dict, dropping keys the dataclass no
    longer declares.

    Rationale: a removed/renamed field in a future release shouldn't lock
    out users whose config.toml still carries the old key. Dataclass
    defaults already cover missing keys; this helper covers extra ones.
    """
    known = {f.name for f in dc_fields(cls)}
    unknown = set(raw_dict) - known
    if unknown:
        logger.warning(
            "config section %s ignoring unknown keys: %s",
            cls.__name__, sorted(unknown),
        )
    return cls(**{k: v for k, v in raw_dict.items() if k in known})


def load_config(path: str | Path = "config.toml") -> AppConfig:
    path = Path(path)
    if not path.exists():
        return AppConfig()
    with open(path, "rb") as f:
        raw = tomli.load(f)
    config = AppConfig()
    if "backend" in raw:
        config.backend = _load_section(BackendConfig, raw["backend"])
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
        # P2-1-S2: warn loudly if user is still on the pre-split [llm] schema.
        # _load_section silently drops these keys, but a missing [llm.local]
        # then quietly falls back to LLMEndpointConfig() defaults — which
        # would silently revert the user's custom model. Better to nudge
        # them in the logs than let it puzzle them later.
        _OLD_LLM_KEYS = {"model", "base_url", "api_key", "provider", "temperature", "max_tokens"}
        stray = _OLD_LLM_KEYS & set(raw_llm)
        if stray and raw_local is None:
            logger.warning(
                "config [llm] uses pre-P2-1-S2 schema (keys: %s); "
                "these are ignored and local LLM defaults will be used. "
                "Move them under [llm.local]. See CHANGELOG for migration.",
                sorted(stray),
            )
        routing = _load_section(LLMRoutingConfig, raw_llm)
        if raw_local is not None:
            routing.local = _load_section(LLMEndpointConfig, raw_local)
        if raw_cloud is not None:
            routing.cloud = _load_section(LLMEndpointConfig, raw_cloud)
        config.llm = routing
    if "asr" in raw:
        config.asr = _load_section(ASRConfig, raw["asr"])
    if "tts" in raw:
        config.tts = _load_section(TTSConfig, raw["tts"])
    if "vad" in raw:
        config.vad = _load_section(VADConfig, raw["vad"])
    if "voice" in raw:
        config.voice = _load_section(VoiceConfig, raw["voice"])
    if "memory" in raw:
        config.memory = _load_section(MemoryConfig, raw["memory"])
    # BillingConfig always resolved — even if [billing] is absent we want a
    # default daily_budget_cny so main.py can construct the ledger.
    db_dir = Path(config.memory.db_path).parent
    config.billing = BillingConfig.from_toml(raw, db_dir=db_dir)
    return config
