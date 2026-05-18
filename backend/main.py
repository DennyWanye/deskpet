from __future__ import annotations

import sys
# Force UTF-8 stdout on Windows (default GBK chokes on emoji in LLM output)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

import asyncio
import os
import re
import secrets
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

import logging
from pathlib import Path as _Path

import structlog
import uvicorn

# P2-2 debug (2026-04-20): Rust supervisor drains child stdout/stderr after
# the SHARED_SECRET handshake, so structlog's console output vanishes once
# the frontend is driving the backend. Mirror everything into
# logs/backend.log via the stdlib logging root so we can tail pipeline
# events (asr_result / vad / lip_sync) without bouncing through the
# supervisor. structlog defaults to using stdlib logging under the hood,
# so configuring the root handler is enough.
_log_dir = _Path(__file__).parent.parent / "logs"
_log_dir.mkdir(exist_ok=True)
_log_file = _log_dir / "backend.log"
_file_handler = logging.FileHandler(_log_file, encoding="utf-8")
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
)
_stream_handler = logging.StreamHandler()
logging.basicConfig(
    level=logging.INFO,
    handlers=[_stream_handler, _file_handler],
    force=True,  # override anything uvicorn may have installed earlier
)

# structlog defaults to its own PrintLogger (stdout only). Point it at
# stdlib logging so the FileHandler above actually receives events.
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.KeyValueRenderer(
            key_order=["event", "level", "timestamp"],
            sort_keys=False,
        ),
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from pathlib import Path
from pydantic import BaseModel, field_validator, model_validator

from config import load_config, resolve_config_path
import paths as _paths
from paths import resolve_model_dir  # P3-S1
from context import ServiceContext
import p4_ipc  # P4-S11 MemoryPanel + ContextTrace IPC handlers
from observability.crash_reports import install_crash_reporter
from observability.metrics import render as render_metrics
from observability.startup import registry as startup_errors  # P3-S2

# Install the uncaught-exception hook as early as possible so import-time
# failures later in this file still land in crash_reports/.
install_crash_reporter()

logger = structlog.get_logger()

# P3-S7: ensure user data / cache / models directories exist before any
# subsystem tries to write into them. Also seeds <user_data>/config.toml
# from the bundle default on first run (via resolve_config_path).
_paths.ensure_user_dirs()

_CONFIG_PATH = resolve_config_path()
config = load_config(_CONFIG_PATH)
logger.info(
    "config_loaded",
    path=str(_CONFIG_PATH),
    exists=_CONFIG_PATH.is_file(),
    user_data_dir=str(_paths.user_data_dir()),
    user_models_dir=str(_paths.user_models_dir()),
    model_root=str(_paths.model_root()),
)
PROJECT_ROOT = _CONFIG_PATH.parent
SHARED_SECRET = secrets.token_hex(16)

service_context = ServiceContext()

# P3-S5 / P4-S20: register the bundled CUDA DLL dir BEFORE any provider
# import that drags in torch (silero_vad / faster_whisper_asr both do).
# torch's `_load_dll_libraries` runs at module import and globs
# `torch/lib/*.dll` with LoadLibrary; if cudart64_12.dll / cublas64_12.dll
# can't be resolved on the DLL search path, `shm.dll` (which links those
# transitively) raises `OSError [WinError 126]` and the frozen exe dies
# before logging a single line. Doing AddDllDirectory here — before the
# `from providers...` block on the next line — fixes that race.
# Was previously below, after vad/asr provider construction. That worked
# in dev (CUDA on system PATH) but blew up in the frozen bundle where
# the only copy of cudart64_12.dll lives in `_internal/ctranslate2/`.
if getattr(sys, "frozen", False):
    try:
        _ct2_dir_early = Path(sys._MEIPASS) / "ctranslate2"  # type: ignore[attr-defined]
        if _ct2_dir_early.is_dir():
            os.add_dll_directory(str(_ct2_dir_early))
    except Exception:  # pragma: no cover — silent; logged after structlog ready
        pass

# --- Register providers ---
from providers.openai_compatible import OpenAICompatibleProvider
from providers.silero_vad import SileroVAD
from providers.faster_whisper_asr import FasterWhisperASR
from providers.edge_tts_provider import EdgeTTSProvider
from providers.cosyvoice_tts import CosyVoice2Provider
from agent.providers.simple_llm import SimpleLLMAgent
from agent.providers.tool_using import ToolUsingAgent
from memory.sensitive_filter import RedactingMemoryStore
from tools.registry import ToolRegistry
from tools.get_time import get_time_tool
from tools.clipboard import read_clipboard_tool
from tools.reminder import list_reminders_tool
from observability.vram import classify_tier, recommend_asr_device
from router.hybrid_router import HybridRouter, LLMUnavailableError, RoutingStrategy
from billing.ledger import BillingLedger

from config import resolve_cloud_api_key as _resolve_cloud_api_key  # P2-1-S3

# P4-S20-LLM-Unified: runtime overrides — settings panel 里改的会写到这个文件，
# 启动时读它覆盖 config.toml [llm] 段（只 base_url / model / temperature；
# api_key 走 keychain 不进文件）。允许用户改完不动 config.toml 也保留。
LLM_RUNTIME_PATH = _paths.user_data_dir() / "llm_runtime.json"


def _load_llm_runtime_overrides() -> dict:
    """Read llm_runtime.json — empty/missing/invalid → {}."""
    if not LLM_RUNTIME_PATH.exists():
        return {}
    try:
        import json as _json
        return _json.loads(LLM_RUNTIME_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm_runtime_overrides_load_failed: %s", exc)
        return {}


def _save_llm_runtime_overrides(data: dict) -> None:
    """Write llm_runtime.json — best-effort, never raise."""
    import json as _json
    try:
        LLM_RUNTIME_PATH.parent.mkdir(parents=True, exist_ok=True)
        LLM_RUNTIME_PATH.write_text(
            _json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8",
        )
        logger.info("llm_runtime_overrides_saved path=%s", LLM_RUNTIME_PATH)
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm_runtime_overrides_save_failed: %s", exc)


# P4-S25 (2026-05-09): cross-endpoint Ollama fallback removed at
# user request. Single-endpoint mode — errors surface directly
# instead of auto-swapping to a different model with a different
# voice mid-turn.

# Apply runtime overrides BEFORE creating local_llm so the very first
# request after a restart uses the user's saved config.
_llm_overrides = _load_llm_runtime_overrides()
if _llm_overrides:
    if "base_url" in _llm_overrides:
        config.llm.local.base_url = _llm_overrides["base_url"]
    if "model" in _llm_overrides:
        config.llm.local.model = _llm_overrides["model"]
    if "temperature" in _llm_overrides:
        config.llm.local.temperature = float(_llm_overrides["temperature"])
    if "api_key" in _llm_overrides and _llm_overrides["api_key"]:
        # plaintext stored in runtime json — usable but not recommended;
        # the proper place is OS keychain via SettingsPanel.
        config.llm.local.api_key = _llm_overrides["api_key"]
    logger.info(
        "llm_runtime_overrides_applied base_url=%s model=%s",
        config.llm.local.base_url, config.llm.local.model,
    )

# P4-S20-LLM-Unified: 统一 LLM endpoint。api_key 解析三档优先级：
#   1. 配置值如果以 "$" 开头 → 视作环境变量名，从 env 读 (e.g. "$OPENAI_API_KEY")
#   2. config.llm.local.api_key 是 "ollama" / "from-keychain" / 空 → 尝试从
#      keychain 通过 DESKPET_CLOUD_API_KEY env 读（向后兼容 P2-1-S3 的 Tauri
#      keychain 注入路径）；拿不到就用配置原值
#   3. 其它情况（用户写了真 key 在 config）→ 直接用配置值
def _resolve_llm_api_key(configured: str) -> str:
    if configured.startswith("$"):
        env_name = configured[1:]
        val = os.environ.get(env_name, "")
        if val:
            logger.info("llm_api_key_from_env env=%s", env_name)
            return val
        logger.warning(
            "llm_api_key_env_not_set env=%s — falling back to placeholder",
            env_name,
        )
        return configured  # placeholder; provider call will 401 if used
    # 占位符触发 keychain 读取
    placeholders = {"ollama", "from-keychain", "from-env", "", "your-key-here"}
    if configured in placeholders:
        keychain_key = _resolve_cloud_api_key()
        if keychain_key:
            logger.info("llm_api_key_from_keychain")
            return keychain_key
        # 占位符 + 没 env → 保持占位符（Ollama 会接受任何值；云端会 401）
    return configured

_resolved_api_key = _resolve_llm_api_key(config.llm.local.api_key)

# 2026-05-17 deepseek-inline-cot-dsml-sanitize Strangler-Fig flag (default
# on). Read once; passed to every OpenAICompatibleProvider so setting
# [llm] sanitize_inline_cot_dsml = false instantly restores legacy raw
# passthrough after a restart (demo rollback).
_sanitize_cot_dsml = bool(
    (config.raw.get("llm") or {}).get("sanitize_inline_cot_dsml", True)
)
logger.info("sanitize_inline_cot_dsml_flag", enabled=_sanitize_cot_dsml)

local_llm = OpenAICompatibleProvider(
    base_url=config.llm.local.base_url,
    api_key=_resolved_api_key,
    model=config.llm.local.model,
    temperature=config.llm.local.temperature,
    sanitize_inline_cot_dsml=_sanitize_cot_dsml,
)

# P4-S25 (2026-05-09): cross-endpoint Ollama fallback removed at user
# request — they don't want auto-swap to gemma4:e4b mid-turn because
# the model-style mismatch produces ugly mixed responses. Errors from
# the configured LLM endpoint now surface directly to the user.

# Keep cloud_llm symbol around for legacy code paths but it's None now —
# config.llm.cloud is no longer set under the unified [llm] schema.
cloud_llm = None
_current_cloud_api_key: str | None = (
    _resolved_api_key if "localhost" not in config.llm.local.base_url else None
)
if config.llm.cloud is not None:
    # 兼容旧 [llm.cloud] schema — 新部署不会走这里。
    _cloud_key = _resolve_cloud_api_key()
    if _cloud_key:
        _current_cloud_api_key = _cloud_key
        cloud_llm = OpenAICompatibleProvider(
            base_url=config.llm.cloud.base_url,
            api_key=_cloud_key,
            model=config.llm.cloud.model,
            temperature=config.llm.cloud.temperature,
            sanitize_inline_cot_dsml=_sanitize_cot_dsml,
        )

# P2-1-S8: BillingLedger — SQLite ledger of every chat_stream call + its
# cost in CNY. Its .create_hook() becomes the BudgetHook HybridRouter gates
# cloud calls through. Local calls bypass the hook entirely (they're free).
billing_ledger = BillingLedger(
    db_path=config.billing.db_path,
    pricing=config.billing.pricing,
    unknown_model_price_cny_per_m_tokens=config.billing.unknown_model_price_cny_per_m_tokens,
    daily_budget_cny=config.billing.daily_budget_cny,
    tz=ZoneInfo(config.billing.tz),
)
service_context.register("billing_ledger", billing_ledger)

# P5-S2 multi-provider-management Phase 2: LLMProviderRegistry
# 单一 source of truth for [[llm.providers]] 列表。Phase 0/1 已实现底层,
# 这里在启动时执行 legacy migration + 构造 registry,放到 service_context
# 供 ws handler (`settings_providers_*`) 读写。
try:
    from llm.provider_registry import (
        LLMProviderRegistry,
        _migrate_legacy_provider_config,
    )
    try:
        _migrate_legacy_provider_config(_CONFIG_PATH)
    except Exception as _exc:  # noqa: BLE001
        logger.warning("provider_registry_migration_failed: %s", _exc)
    _provider_registry = LLMProviderRegistry(_CONFIG_PATH)
    service_context.register("provider_registry", _provider_registry)
    logger.info(
        "provider_registry_initialized providers=%d",
        len(_provider_registry.list_providers()),
    )
except Exception as _exc:  # noqa: BLE001
    logger.warning("provider_registry_init_failed: %s", _exc)

llm = HybridRouter(
    local=local_llm,
    cloud=cloud_llm,
    strategy=RoutingStrategy(config.llm.strategy),
    # P2-1-S8: BillingLedger's hook debits cloud spend and denies cloud
    # calls once daily_budget_cny is exhausted. Local calls bypass the
    # hook entirely (free). See spec §1.1 / §2.4.
    budget_hook=billing_ledger.create_hook(),
)
service_context.register("llm_engine", llm)

# P4-S17: SessionDB is the single conversation source of truth.
# The P4 wire-in below wraps it in RedactingMemoryStore, then agent_engine
# is constructed with that final memory_store.
memory_store = None

# V5 §2.3: agent_engine 与 llm_engine 分层。
# 组装栈:ToolUsingAgent(S3) 包装 SimpleLLMAgent(S2 + S0), memory 在内层。
# 工具调用的结果是 inline 注入 user-facing stream,不走 memory 持久化。
tool_registry = ToolRegistry()
tool_registry.register(get_time_tool)
tool_registry.register(read_clipboard_tool)
tool_registry.register(list_reminders_tool)
service_context.register("tool_router", tool_registry)

# P4-S20: v2 deskpet.tools.registry singleton — full schema-aware
# registry used by the new tool_use agent loop. Hosts the 7 OS tools
# (read/write/edit/list/shell/web/desktop_create_file) plus the
# auto-discovered file/web/memory tools from earlier slices.
# PermissionGate is wired with the control-WS responder so user
# popups appear before any sensitive op runs.
try:
    from deskpet.tools.registry import registry as deskpet_tool_registry_v2
    from deskpet.tools.os_tools import register_os_tools as _register_os_tools_v2
    from deskpet.permissions.gate import (
        PermissionGate as _PermissionGate,
        PermissionGateConfig as _PermissionGateConfig,
    )
    from deskpet.types.skill_platform import (
        PermissionResponse as _PermissionResponse,
    )
    _register_os_tools_v2(deskpet_tool_registry_v2)
    # P4-S22: Code mode tools (glob, grep, web_search) registered now;
    # todo_write + agent need closures over runtime objects (SessionDB,
    # LLM shim) and are wired later, after those are constructed.
    from deskpet.tools.code_tools import register_code_tools as _register_code_tools
    _register_code_tools(deskpet_tool_registry_v2)
    _shell_deny = list(getattr(getattr(config, "permissions", None), "deny", {}).get("shell_patterns", []) or [
        "rm -rf /",
        "format c:",
        "del /f /s /q c:",
    ])
    permission_gate_v2 = _PermissionGate(
        config=_PermissionGateConfig(
            timeout_s=60.0,
            shell_deny_patterns=_shell_deny,
        )
    )
    deskpet_tool_registry_v2.set_permission_gate(permission_gate_v2)
    # P4-S25: persist auto_mode across restart. Path lives under the
    # user data dir so it follows the user's profile (dev mode uses
    # `<repo>/userdata/`, prod uses `%APPDATA%/deskpet/`). Loading
    # happens immediately so by the time the first chat runs, the
    # gate already knows the user's prior choice.
    try:
        from pathlib import Path as _Path
        _automode_path = _Path(_paths.user_data_dir()) / "permissions_auto_mode.json"
        permission_gate_v2.bind_persistence_path(_automode_path)
        _restored_automode = permission_gate_v2.load_auto_mode()
        if _restored_automode:
            logger.info("permission_auto_mode_restored", enabled=True)
    except Exception as _exc:  # noqa: BLE001
        logger.warning("auto_mode_persist_init_failed: %s", _exc)
    # Module-level globals (service_context has a pre-declared key list
    # we don't want to extend just for this).
    # Accessed by the chat handler when it instantiates AgentLoop.

    # P4-S20 Stage C — marketplace installer + registry client.
    from deskpet.skills.marketplace import (
        RegistryClient as _RegistryClient,
        SkillInstaller as _SkillInstaller,
        SafetyError as _MarketplaceSafetyError,
    )
    _user_skills_dir = _paths.user_data_dir() / "skills"
    _staging_dir = _paths.user_data_dir() / "_skill_staging"
    _user_skills_dir.mkdir(parents=True, exist_ok=True)
    _staging_dir.mkdir(parents=True, exist_ok=True)
    _registry_url = (
        getattr(getattr(config, "marketplace", None), "registry_url", None)
        or "https://raw.githubusercontent.com/DennyWanye/deskpet/master/docs/skills-registry.json"
    )
    skill_registry_client = _RegistryClient(
        url=_registry_url,
        cache_ttl_s=3600.0,
    )
    skill_installer = _SkillInstaller(
        skills_dir=_user_skills_dir,
        staging_dir=_staging_dir,
        known_tools=set(deskpet_tool_registry_v2.list_tools()),
    )
    # In-memory pending stage map — UI confirms by staging_id.
    _skill_staged: dict[str, "Any"] = {}

    # P4-S20 Stage D — plugin system.
    from deskpet.plugins import PluginManager as _PluginManager
    _plugins_dir = _paths.user_data_dir() / "plugins"
    _plugins_dir.mkdir(parents=True, exist_ok=True)
    _enabled_plugins = list(
        getattr(getattr(config, "plugins", None), "enabled", []) or []
    )
    plugin_manager = _PluginManager(
        plugins_dir=_plugins_dir,
        enabled=_enabled_plugins or None,  # None → default-enable all
    )
    plugin_manager.discover()
    logger.info(
        "p4_s20_plugins_discovered",
        plugins_dir=str(_plugins_dir),
        plugins=[p["name"] for p in plugin_manager.list_plugins()],
    )
    # Per-session pending request map: request_id → asyncio.Future.
    # Filled by the gate responder, drained by the WS handler when
    # a permission_response arrives.
    _permission_pending: dict[str, "asyncio.Future"] = {}

    async def _permission_responder(req):  # PermissionRequest → PermissionResponse
        """Broadcast permission_request via the control WS for the request's session,
        await matching permission_response. Falls back to deny on timeout/disconnect."""
        ws = _control_connections.get(req.session_id) or _control_connections.get("default")
        if ws is None:
            return _PermissionResponse(request_id=req.request_id, decision="deny")
        loop = asyncio.get_running_loop()
        fut: "asyncio.Future[_PermissionResponse]" = loop.create_future()
        _permission_pending[req.request_id] = fut
        try:
            await ws.send_json({
                "type": "permission_request",
                "payload": {
                    "request_id": req.request_id,
                    "category": req.category,
                    "summary": req.summary,
                    "params": req.params,
                    "default_action": req.default_action,
                    "dangerous": req.dangerous,
                    "session_id": req.session_id,
                },
            })
            return await fut
        finally:
            _permission_pending.pop(req.request_id, None)

    permission_gate_v2.set_responder(_permission_responder)
    logger.info(
        "p4_s20_tool_registry_v2_ready",
        os_tools=len(deskpet_tool_registry_v2.list_tools(source="builtin")),
    )
except Exception as _v2_exc:  # noqa: BLE001 — non-fatal, log + degrade
    logger.warning("p4_s20_v2_init_failed", error=str(_v2_exc))
    deskpet_tool_registry_v2 = None
    permission_gate_v2 = None
    _permission_pending = {}
    skill_registry_client = None
    skill_installer = None
    _skill_staged = {}
    plugin_manager = None

# S8 (R9): log the current hardware tier once so the dispatch decision is
# visible in the startup banner. The tier itself doesn't force provider
# swaps yet — that's Phase 2 work when we ship multiple LLM/TTS binaries.
_tier = classify_tier()
logger.info(
    "hardware_tier",
    tier=_tier.tier,
    recommended_llm=_tier.llm_model,
    recommended_tts=_tier.tts_model,
    recommended_asr=_tier.asr_model,
)

vad = SileroVAD(
    threshold=config.vad.threshold,
    min_speech_ms=config.vad.min_speech_ms,
    min_silence_ms=config.vad.min_silence_ms,
)
service_context.register("vad_engine", vad)

# P3-S5: frozen bundle ships ctranslate2 + minimal CUDA DLLs
# (cublas/cublasLt/cudart/nvrtc, ~450 MB) under _internal/ctranslate2/.
# Register that dir so cublas64_12.dll resolves at first transcribe.
# ctranslate2 itself already calls AddDllDirectory on its own dir at import,
# but that happens only when ctranslate2 is imported — we do it eagerly
# so nothing races with torch's own DLL probe.
if getattr(sys, "frozen", False):
    try:
        _ct2_dir = Path(sys._MEIPASS) / "ctranslate2"  # type: ignore[attr-defined]
        if _ct2_dir.is_dir():
            os.add_dll_directory(str(_ct2_dir))
            logger.info("cuda_dll_dir_registered", path=str(_ct2_dir))
    except Exception as e:  # pragma: no cover — best-effort
        logger.warning("cuda_dll_dir_register_failed", error=str(e))

# S4: device="auto" in config.toml → pick cuda/cpu based on detected VRAM.
# Explicit "cuda" or "cpu" is respected verbatim (user override).
if config.asr.device == "auto":
    _asr_device, _asr_compute = recommend_asr_device()
    logger.info("asr_device_selected", device=_asr_device, compute=_asr_compute, source="auto")
else:
    _asr_device, _asr_compute = config.asr.device, config.asr.compute_type

asr = FasterWhisperASR(
    model=config.asr.model,
    device=_asr_device,
    compute_type=_asr_compute,
    local_dir=str(resolve_model_dir(config.asr.model_dir)),  # P3-S1
    hotwords=config.asr.hotwords,  # P2-2-F1: short-phrase logit bias
)
service_context.register("asr_engine", asr)

# S9 (R11): TTS provider selection. "cosyvoice2" tries local first, with
# built-in edge-tts fallback on any failure (see CosyVoice2Provider.load).
# "edge-tts" (or anything else) goes straight to the cloud voice.
if config.tts.provider == "cosyvoice2":
    # P3-S1: model_dir is a bare subfolder name under paths.model_root();
    # resolve_model_dir handles dev-mode + PyInstaller + env override.
    tts = CosyVoice2Provider(
        model_dir=str(resolve_model_dir(config.tts.model_dir)),
        fallback_voice=config.tts.voice,
    )
else:
    tts = EdgeTTSProvider(voice=config.tts.voice)
service_context.register("tts_engine", tts)
# P4-S21 #13: hand the TTS to the permission gate so voice-context
# requests get an audible "please click Allow" cue alongside the popup.
# permission_gate_v2 was constructed earlier (line ~301) and may be
# None when v2 stack init failed.
try:
    if permission_gate_v2 is not None:
        permission_gate_v2.set_tts_engine(tts)
except NameError:
    # permission_gate_v2 didn't get initialized (e.g. import error in
    # the v2 block). Voice prompts skip TTS narration; popup still works.
    pass

# --- P4-S13: read-only P4 services (FileMemory + SkillLoader + MemoryManager) ---
#
# We construct the three "safe" components at module top-level so p4_ipc.py
# handlers (skills_list / memory_l1_list / memory_l1_delete) return real data
# instead of the pre-S13 graceful-empty stub. ContextAssembler + MCPManager
# stay deferred to a later slice because they require deeper hooks into the
# chat stream and external processes respectively.
#
# Everything is best-effort: any failure logs a warning + leaves the slot
# empty. p4_ipc.py's graceful fallback then surfaces `reason: *_not_registered`
# to the UI, which keeps the panels usable.
try:
    from deskpet.memory.file_memory import FileMemory as _FileMemory
    from deskpet.memory.manager import MemoryManager as _MemoryManager
    from deskpet.memory.session_db import SessionDB as _SessionDB
    from deskpet.memory.embedder import Embedder as _Embedder
    from deskpet.memory.vector_worker import VectorWorker as _VectorWorker
    from deskpet.memory.image_worker import (
        ImageGenerationWorker as _ImageWorker,
    )
    from deskpet.memory.retriever import Retriever as _Retriever
    from deskpet.skills.loader import SkillLoader as _SkillLoader

    # L1 lives under the same data dir as memory.db → already resolved by
    # load_config() into an absolute path. paths.user_data_dir() is the
    # canonical root when memory.db_path was blank.
    _l1_dir = Path(config.memory.db_path).resolve().parent if config.memory.db_path else _paths.user_data_dir() / "data"
    _file_memory = _FileMemory(base_dir=_l1_dir)
    service_context.register("file_memory", _file_memory)

    # P4-S15: Embedder — BGE-M3 INT8 with mock fallback when the model dir
    # is absent. Mock embedder hits is_ready=True instantly so cold-start
    # isn't blocked even on a fresh install. Real model loads in the
    # background via lifespan.warmup() so prompt cache stays hot.
    try:
        _bge_dir = resolve_model_dir("bge-m3-int8")
    except Exception:
        _bge_dir = None
    _embedder = _Embedder(
        model_path=_bge_dir,
        use_mock_when_missing=True,
    )

    # P4-S15: SessionDB at <data>/state.db, side-by-side with the legacy
    # memory.db. on_message_written hook will be wired to VectorWorker.enqueue
    # in lifespan once the worker has started, so embeddings backfill
    # automatically as new turns hit the DB.
    _state_db_path = _l1_dir / "state.db"
    _session_db = _SessionDB(db_path=_state_db_path)

    # P4-S15: VectorWorker — drains a queue of (msg_id, text) into the
    # vec0 virtual table on a 1s interval. Stays empty until SessionDB
    # actually receives writes, so the cold-start cost is essentially nil.
    _vector_worker = _VectorWorker(
        session_db=_session_db,
        embedder=_embedder,
    )

    # P4-S15: Retriever — RRF fusion of vec / fts / recency / salience.
    # Embedder may still be loading; Retriever skips the vec route until
    # embedder.is_ready becomes True. Other routes work immediately.
    _retriever = _Retriever(
        session_db=_session_db,
        embedder=_embedder,
    )

    # P4-S17: MemoryManager and agent memory share SessionDB as the
    # canonical L2/conversation store.
    # OpenSpec 2026-05-16-companion-context-isolation §D1/D4: inject the
    # companion cross-session decay so the retriever down-weights an
    # unrelated code-session project memory when serving a companion
    # request. Absent / =1.0 → legacy behaviour (Strangler-Fig).
    _comp_cfg = (config.raw.get("companion") if hasattr(config, "raw") else None) or {}
    _xsess_decay = _comp_cfg.get("memory_cross_session_decay")
    _memory_manager = _MemoryManager(
        file_memory=_file_memory,
        session_db=_session_db,
        retriever=_retriever,
        cross_session_decay=(
            float(_xsess_decay) if _xsess_decay is not None else None
        ),
    )
    service_context.register("memory_manager", _memory_manager)

    # P4-S17: RedactingMemoryStore remains the only write path exposed to
    # the agent/admin API, but the inner store is now the canonical state.db.
    memory_store = RedactingMemoryStore(_session_db)
    service_context.register("memory_store", memory_store)

    # SkillLoader: explicitly point dir[0] at the package-data builtin dir so
    # the three shipped skills (recall-yesterday / summarize-day / weather-
    # report) are found without needing a user-dir copy step. dir[1] is the
    # user's override dir under %AppData%/deskpet/skills/user.
    # enable_watch=False in rc1 to avoid the watchdog thread on cold boot;
    # UI's refresh button triggers a manual reload via list_skills() anyway.
    import deskpet.skills.builtin as _builtin_pkg
    _builtin_dir = Path(_builtin_pkg.__file__).parent
    _user_skills_dir = _paths.user_data_dir() / "skills" / "user"
    _user_skills_dir.mkdir(parents=True, exist_ok=True)
    _skill_loader = _SkillLoader(
        skill_dirs=[_builtin_dir, _user_skills_dir],
        enable_watch=False,
    )
    service_context.register("skill_loader", _skill_loader)

    # P4-S14 + S15: ContextAssembler — pass embedder so TaskClassifier can
    # use the embed-tier route (rule → embed → llm cascade). When BGE-M3
    # isn't loaded yet, the embed path silently falls through to default —
    # graceful degradation already implemented in the classifier.
    from deskpet.agent.assembler import build_default_assembler as _build_assembler
    _assembler = _build_assembler(
        embedder=_embedder,
        llm_registry=None,
        enabled=True,
        context_window=32_000,
        budget_ratio=0.6,
    )
    service_context.register("context_assembler", _assembler)

    # P4-S16: 这三个 handle 升级成正式注册服务（之前挂私有 _p4_* 属性）。
    # context.py 已把名字加进 _VALID_SERVICES。lifespan 通过 sc.get(name) 拉。
    service_context.register("session_db", _session_db)
    service_context.register("vector_worker", _vector_worker)
    service_context.register("embedder", _embedder)

    # OpenSpec 2026-05-16-async-image-gen: ImageGenerationWorker —
    # generate_image submits a job and returns instantly; this worker
    # does the slow chinzy POST + retry + save + open in the background
    # and pushes the result back to the pet via _image_notifier (reuses
    # the control-ws chat_v2_final path → zero frontend change, petText
    # cleaning applies). async_enabled=false → not started (tool falls
    # back to legacy sync blocking).
    _img_cfg = (config.raw.get("image") if hasattr(config, "raw") else None) or {}

    async def _image_notifier(_sid: str, _text: str) -> None:
        # 1) push as chat_v2_final to the originating session's control
        #    conn (fallback: default / broadcast all, mirrors auto_resume).
        _payload = {"type": "chat_v2_final", "payload": {"text": _text}}
        _wsobj = (
            _control_connections.get(_sid)
            or _control_connections.get("default")
        )
        try:
            if _wsobj is not None:
                await _wsobj.send_json(_payload)
            else:
                for _w in list(_control_connections.values()):
                    try:
                        await _w.send_json(_payload)
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as _nex:  # noqa: BLE001
            logger.debug("image_notifier_ws_failed sid=%s err=%s", _sid, _nex)
        # 2) persist to SessionDB as an assistant message so
        #    ChatHistoryPanel / L2 recall have it (same as a normal reply).
        try:
            _sdb_n = service_context.get("session_db")
            if _sdb_n is not None:
                await _sdb_n.append_message(
                    session_id=_sid, role="assistant", content=_text,
                )
        except Exception as _pex:  # noqa: BLE001
            logger.debug("image_notifier_persist_failed err=%s", _pex)

    _image_worker = _ImageWorker(
        notifier=_image_notifier,
        max_concurrent=int(_img_cfg.get("max_concurrent", 2)),
    )
    service_context.register("image_worker", _image_worker)

    # P4-S22: Code Mode session manager (per-base-session enable map).
    from deskpet.code_mode import CodeModeManager as _CodeModeManager
    _code_mode_manager = _CodeModeManager()
    # P4-S25 B4: bind SessionDB so projects persist across restart.
    # Actual `load_persisted` runs inside the async lifespan (see below)
    # because we can't await at module level.
    _code_mode_manager.bind_persistence(_session_db)
    service_context.register("code_mode", _code_mode_manager)

    # P5-S1: supervisor watchdog infrastructure. SessionActivity tracks
    # AgentEvent timestamps + tool-call signature windows so the watchdog
    # can detect "stuck" Code-mode sessions. Watchdog itself is started
    # later in lifespan() with a 30s grace period; here we just register
    # the data structures so the WS forwarder can bump them.
    from agent.session_activity import SessionActivityStore as _SessionActivityStore
    _session_activity_store = _SessionActivityStore()
    service_context.register("session_activity", _session_activity_store)

    # P5-S4: NudgeQueue is created here (not in lifespan) so the IPC
    # handlers — code_mode_exit, message build path — can reach it
    # without nullable plumbing. Capacity from [supervisor].max_hints_per_session.
    from agent.nudge_queue import NudgeQueue as _NudgeQueue
    _nudge_queue = _NudgeQueue(
        cap=int((config.raw.get("supervisor") or {}).get("max_hints_per_session", 3))
    )
    service_context.register("nudge_queue", _nudge_queue)

    # P4-S22: register the two Code-mode tools that need closures over
    # runtime objects (SessionDB for todo_write, LLM shim + tool
    # registry for agent). The other three (glob/grep/web_search) were
    # registered earlier with static handlers.
    if deskpet_tool_registry_v2 is not None:
        try:
            from deskpet.tools.code_tools import (
                build_todo_write_tool as _build_todo_write_tool,
                build_agent_tool as _build_agent_tool,
            )
            from agent.tool_use_shim import (  # type: ignore[import-not-found]
                OpenAICompatibleAgentLLM as _ShimForAgent,
            )

            # todo_write needs (session_db, code_session_id_resolver, broadcaster)
            # P4-S22 fix: wire a real broadcaster that pushes
            # `code_todo_update` to whichever control WebSocket owns
            # the active code-mode session. Without this, todos write to
            # DB but the frontend's TodoListPanel doesn't update until
            # the user manually closes / reopens the panel (or sends a
            # new chat turn that re-pulls). Now they update live.
            def _resolve_code_sid() -> str | None:
                # The chat handler stores the active session id on the
                # tool registry's session_context dict — pull it from
                # there. If multiple sessions are concurrent this picks
                # the most-recent one written; for now there's only ever
                # one chat session active per backend process.
                cm = service_context.get("code_mode")
                if cm is None:
                    return None
                # Last writer wins — code_mode's all_sessions() returns
                # all base sessions; pick any enabled one.
                for base_sid, st in cm.all_sessions().items():
                    if st.enabled and st.code_session_id:
                        return st.code_session_id
                return None

            def _resolve_base_sid_for_code_session(code_sid: str) -> str | None:
                """Reverse map: code session id → base session id, so we
                know which control_ws to broadcast to. Useful when the
                tool runs deep inside AgentLoop and only knows the
                code_session_id."""
                cm = service_context.get("code_mode")
                if cm is None:
                    return None
                for base_sid, st in cm.all_sessions().items():
                    if st.code_session_id == code_sid:
                        return base_sid
                return None

            async def _todo_broadcaster(msg: dict) -> None:
                """Broadcast a `code_todo_update` to ALL connected
                control WebSockets so both the pet and the code panel
                update in lockstep. Multi-window setups (P4-S23) need
                this — a single-target send to the chat-trigger ws
                misses the sibling window even though both render
                the same code session.
                Best-effort: WS errors are swallowed so a stale tab
                doesn't break tool execution.
                """
                # Snapshot to avoid mutation during iteration when a
                # ws closes mid-broadcast.
                targets = list(_control_connections.values())
                for ws in targets:
                    try:
                        await ws.send_json(msg)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("code_todo_broadcast_failed", error=str(exc))

            _todo_handler, _todo_schema = _build_todo_write_tool(
                session_db=_session_db,
                code_session_id_resolver=_resolve_code_sid,
                broadcaster=_todo_broadcaster,
            )

            _shim_for_agent = _ShimForAgent(provider=local_llm)

            def _resolve_parent_sid() -> str:
                cm = service_context.get("code_mode")
                if cm is not None:
                    for base_sid, st in cm.all_sessions().items():
                        if st.enabled:
                            return base_sid
                return "default"

            _agent_handler, _agent_schema = _build_agent_tool(
                llm_shim=_shim_for_agent,
                parent_tool_registry=deskpet_tool_registry_v2,
                parent_session_id_resolver=_resolve_parent_sid,
            )

            # Re-register the full code tool set including the closures.
            from deskpet.tools.code_tools import (
                register_code_tools as _register_code_tools_full,
            )
            _register_code_tools_full(
                deskpet_tool_registry_v2,
                todo_write_handler=_todo_handler,
                todo_write_schema=_todo_schema,
                agent_handler=_agent_handler,
                agent_schema=_agent_schema,
            )
            # P5-S2 G1: count bumped 5→6 — added fetch_tool_result
            logger.info("p4_s22_code_tools_registered", count=6)
        except Exception as _ct_exc:  # noqa: BLE001
            logger.warning(
                "p4_s22_code_tools_register_failed",
                error=str(_ct_exc),
            )

    logger.info(
        "p4_services_registered",
        l1_dir=str(_l1_dir),
        state_db=str(_state_db_path),
        memory_manager=True,
        skill_loader=True,
        context_assembler=True,
        embedder_mock_when_missing=True,
        vector_worker=True,
        retriever=True,
    )

    # P4-S20-D: 把 _state_db_path 暴露给 IPC handler 用 (memory_summarize_now)
    _summarizer_state_db_path = _state_db_path
except Exception as _p4_exc:
    # S13 stay-alive guarantee: ANY P4 import/init failure must not block the
    # legacy chat path. p4_ipc.py already handles None services gracefully.
    logger.warning(
        "p4_services_registration_failed",
        error=str(_p4_exc),
        error_type=type(_p4_exc).__name__,
    )
    _summarizer_state_db_path = None  # type: ignore[assignment]


base_agent = SimpleLLMAgent(llm, memory=memory_store)
agent = ToolUsingAgent(base=base_agent, registry=tool_registry)
service_context.register("agent_engine", agent)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Preload models on startup (best-effort — failures logged but don't block)."""
    logger.info("preloading models...")
    # P2-1-S8: billing DB must exist before the first chat call. Failure
    # here is logged but doesn't block startup — the ledger simply won't
    # record anything until the DB is reachable on a future boot.
    try:
        await billing_ledger.init()
        logger.info("billing_ledger_ready", db_path=str(config.billing.db_path))
    except Exception as exc:
        logger.warning("billing_ledger_init_failed", error=str(exc))
    for name in ("vad_engine", "asr_engine", "tts_engine"):
        engine = service_context.get(name)
        if engine and hasattr(engine, "load"):
            try:
                await engine.load()
                logger.info("loaded", engine=name)
            except Exception as exc:
                logger.warning("failed_to_load", engine=name, error=str(exc))
                # P3-S2: persist structured error so /health + WS startup_status
                # can surface "degraded" state instead of silently accepting
                # requests that will later 500.
                startup_errors.record(name, exc)
    # P4-S13: async initialisers for the P4 read-only stack. Each failure is
    # isolated — MemoryManager needing a base dir doesn't prevent SkillLoader
    # from scanning the built-in dir, etc.
    _sdb = service_context.get("session_db")
    if _sdb is not None:
        try:
            await _sdb.initialize()
            logger.info("p4_session_db_ready", path=str(_sdb._db_path))
        except Exception as exc:
            logger.warning("p4_session_db_init_failed", error=str(exc))
    # P4-S25 B4: restore persisted code-mode projects from SessionDB.
    # Done after _sdb.initialize() so migration v13 (code_sessions
    # table) is in place. Failure is non-fatal — user just sees an
    # empty project list and can re-add manually.
    _cmm_for_restore = service_context.get("code_mode")
    if _cmm_for_restore is not None and _sdb is not None:
        try:
            _restored_n = await _cmm_for_restore.load_persisted(_sdb)
            logger.info(
                "code_sessions_restored",
                count=_restored_n,
            )
        except Exception as exc:
            logger.warning("code_sessions_restore_failed", error=str(exc))
    _mm = service_context.get("memory_manager")
    if _mm is not None:
        try:
            await _mm.initialize()
            logger.info("p4_memory_manager_ready")
        except Exception as exc:
            logger.warning("p4_memory_manager_init_failed", error=str(exc))
    _sl = service_context.get("skill_loader")
    if _sl is not None:
        try:
            await _sl.start()
            logger.info("p4_skill_loader_ready", count=len(_sl.list_skills()))
        except Exception as exc:
            logger.warning("p4_skill_loader_start_failed", error=str(exc))
    # P4-S15: Embedder warmup runs in the background so cold-start isn't
    # blocked by 286 MB of BGE-M3 weights. Mock fallback returns instantly.
    _emb = service_context.get("embedder")
    if _emb is not None:
        async def _embedder_warmup_bg() -> None:
            try:
                await _emb.warmup()
                logger.info("p4_embedder_ready", is_mock=_emb.is_mock())
            except Exception as exc:
                logger.warning("p4_embedder_warmup_failed", error=str(exc))
        # fire-and-forget; we deliberately don't await
        asyncio.create_task(_embedder_warmup_bg())
    # P4-S15: VectorWorker — starts after SessionDB is initialised so the
    # vec0 schema is in place. After start, wire its enqueue() onto the
    # SessionDB write-hook so new chat turns auto-embed.
    _vw = service_context.get("vector_worker")
    if _vw is not None and _sdb is not None:
        try:
            await _vw.start()
            _sdb._on_message_written = _vw.enqueue  # type: ignore[attr-defined]
            logger.info("p4_vector_worker_ready")
        except Exception as exc:
            logger.warning("p4_vector_worker_start_failed", error=str(exc))
    # OpenSpec 2026-05-16-async-image-gen: start the ImageGenerationWorker
    # unless async disabled (then generate_image runs legacy sync).
    _iw = service_context.get("image_worker")
    _img_async = bool(
        ((config.raw.get("image") if hasattr(config, "raw") else None) or {})
        .get("async_enabled", True)
    )
    if _iw is not None and _img_async:
        try:
            await _iw.start()
            logger.info("image_worker_ready")
        except Exception as exc:  # noqa: BLE001
            logger.warning("image_worker_start_failed", error=str(exc))
    # P4-S15: MCPManager — bootstrap from raw [mcp] section. start() is
    # tolerant: missing section / disabled servers / spawn failures are all
    # logged but don't raise. Only the manager handle is registered; the
    # actual server states are inspectable via manager.server_state().
    try:
        # P4-S18: ensure workspace dir exists before spawning filesystem MCP
        # server. Without this, npx @modelcontextprotocol/server-filesystem
        # spawns OK but its first stat() fails with ENOENT, MCP transport
        # closes, and our manager spins in reconnect loop forever (logged
        # every few seconds, polluting startup output). Touching the dir
        # is idempotent and cheap; agents are still scoped to it.
        try:
            _ws_dir = _paths.user_data_dir() / "workspace"
            _ws_dir.mkdir(parents=True, exist_ok=True)
        except Exception as _ws_exc:  # pragma: no cover — best-effort
            logger.warning("workspace_mkdir_failed", error=str(_ws_exc))

        from deskpet.mcp.bootstrap import create_and_start_from_config as _mcp_bootstrap
        # P6 bugfix 2026-05-13: MCP manager 用的是 deskpet.tools.registry.ToolRegistry
        # 的 keyword API (register(name=..., toolset=..., schema=..., handler=...))，
        # 不是老的 tools.registry.ToolRegistry (register(tool) 单参)。传错 registry
        # 会导致每个 MCP 工具都 TypeError("unexpected keyword 'name'") 注册失败。
        _mcp_manager = await _mcp_bootstrap(
            app_config=config.raw,
            tool_registry=deskpet_tool_registry_v2,
        )
        service_context.register("mcp_manager", _mcp_manager)
        logger.info("p4_mcp_manager_ready", states=_mcp_manager.server_state())
    except Exception as exc:
        logger.warning("p4_mcp_manager_bootstrap_failed", error=str(exc))

    # P4-S20-D: 启动时把"老对话总结"任务 fire-and-forget。不阻塞 startup
    # — 后台慢慢跑。第一次启动 4881 条历史时只处理 max_per_run 个 session
    # 防止打爆 LLM。
    if _summarizer_state_db_path is not None and local_llm is not None:
        async def _bg_summarize() -> None:
            try:
                from deskpet.memory.summarizer import (
                    summarize_old_sessions, make_llm_call,
                )
                # 多等 8s 让其它启动任务先就位（embedder warmup、MCP 启动等）
                await asyncio.sleep(8.0)
                logger.info("summarizer_starting age_days=30 min_messages=20 max_per_run=10")
                result = await summarize_old_sessions(
                    db_path=_summarizer_state_db_path,
                    llm_call=make_llm_call(local_llm),
                    vector_worker=service_context.get("vector_worker"),
                )
                logger.info(
                    "summarizer_done scanned=%d summarized=%d archived=%d errors=%d",
                    result.sessions_scanned,
                    result.sessions_summarized,
                    result.messages_archived,
                    len(result.errors),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "summarizer_bg_failed: %s",
                    exc,
                )
        asyncio.create_task(_bg_summarize())

    # P5-S1/S2: supervisor watchdog + LLM agent. Starts after the rest of
    # startup is done so the 30s grace can run while normal startup races
    # finish. Disabled when [supervisor].enabled = false; in that case we
    # skip construction entirely so the asyncio.Task isn't even created.
    try:
        _sup_cfg = (config.raw.get("supervisor") if hasattr(config, "raw") else None) or {}
        if bool(_sup_cfg.get("enabled", True)):
            from agent.watchdog import WatchdogLoop as _WatchdogLoop
            from agent.supervisor import (
                SupervisorAgent as _SupAgent,
                build_supervisor_hook as _build_sup_hook,
            )
            from agent.snapshot import build_snapshot as _build_snap_func

            # P5-S2 Phase 6: wire per-(sid, tool) circuit breaker into
            # the v2 tool registry. The registry checks ``can_call``
            # before every dispatch and ``record_call`` after, so the
            # breaker doesn't need to be reachable from chat handlers
            # directly. Knobs come from [supervisor] so they live next
            # to the rest of the self-healing config.
            try:
                from agent.circuit_breaker import ToolCircuitBreaker as _ToolBreaker
                if deskpet_tool_registry_v2 is not None:
                    _breaker = _ToolBreaker(
                        threshold=int(_sup_cfg.get("circuit_breaker_threshold", 3)),
                        cooldown_seconds=float(
                            _sup_cfg.get("circuit_breaker_cooldown_seconds", 60)
                        ),
                    )
                    deskpet_tool_registry_v2.set_circuit_breaker(_breaker)
                    service_context.register("tool_circuit_breaker", _breaker)
                    logger.info(
                        "p5s2_circuit_breaker_wired threshold=%d cooldown=%.0fs",
                        int(_sup_cfg.get("circuit_breaker_threshold", 3)),
                        float(_sup_cfg.get("circuit_breaker_cooldown_seconds", 60)),
                    )
            except Exception as _exc:  # noqa: BLE001
                logger.warning("p5s2_circuit_breaker_wire_failed err=%s", _exc)

            # Snapshot builder closure — pulls services lazily so each
            # tick reads fresh state.
            async def _snap_builder(sid: str):
                _ctx_window = int(
                    (config.raw.get("agent") or {}).get("context_window_tokens", 200_000)
                )
                return await _build_snap_func(
                    sid,
                    session_activity=service_context.get("session_activity"),
                    session_db=service_context.get("session_db"),
                    code_mode_manager=service_context.get("code_mode"),
                    context_window_tokens=_ctx_window,
                )

            # Audit closure — persists every non-wait SupervisorAction
            async def _audit_action(action, sid):
                _sdb_for_audit = service_context.get("session_db")
                if _sdb_for_audit is None:
                    return
                hint_text = action.hint_for_main_agent or action.user_message or action.diagnosis
                await _sdb_for_audit.append_supervisor_hint(
                    session_id=sid,
                    alert_id=action.alert_id,
                    hint_text=hint_text,
                    action="cancel_coerced" if action.raw_action == "cancel" else action.action,
                    severity=action.severity,
                    diagnosis=action.diagnosis,
                )

            # Nudge push closure — wraps SupervisorAction → Hint and
            # forwards to the queue.
            async def _push_hint(sid: str, action):
                from agent.nudge_queue import Hint as _Hint
                _nq = service_context.get("nudge_queue")
                if _nq is None:
                    return
                await _nq.push(
                    sid,
                    _Hint(
                        text=action.hint_for_main_agent or action.user_message or "",
                        alert_id=action.alert_id,
                        severity=action.severity,
                    ),
                )

            # Broadcast closure — sends supervisor_alert to all control WS.
            async def _broadcast_supervisor_alert(typ: str, payload: dict):
                # Use the same multi-WS fan-out the todo broadcaster uses.
                # _control_connections is the canonical mapping (sid → ws).
                if not _control_connections:
                    return
                msg = {"type": typ, "payload": payload}
                for _sid_key, _ws_obj in list(_control_connections.items()):
                    try:
                        await _ws_obj.send_json(msg)
                    except Exception as _bex:
                        logger.debug("supervisor_alert_broadcast_failed sid=%s error=%s", _sid_key, _bex)

            # Build agent. Provider resolution (multi-provider-management):
            # 1) `[supervisor].provider_id = "<id>"` in config.toml — explicit pin
            # 2) Else first enabled provider from LLMProviderRegistry.get_chain()
            # 3) Else legacy `local_llm` (single-provider mode, pre-multi-provider)
            #
            # Previously hardcoded `local_llm` which on a multi-provider deploy
            # without Ollama running produced "supervisor_unavailable: ConnectError"
            # — confusing because the main chat works fine via chinzy. Now
            # supervisor follows the user's chain by default.
            _sup_provider = None
            try:
                _reg_for_sup = service_context.get("provider_registry")
                if _reg_for_sup is not None:
                    _sup_pinned_id = (_sup_cfg.get("provider_id") or "").strip()
                    _sup_entry = None
                    if _sup_pinned_id:
                        _sup_entry = _reg_for_sup.get_entry(_sup_pinned_id)
                        if _sup_entry is None or not getattr(_sup_entry, "enabled", True):
                            logger.warning(
                                "supervisor_pinned_provider_missing pid=%s — falling back to chain",
                                _sup_pinned_id,
                            )
                            _sup_entry = None
                    if _sup_entry is None:
                        _chain = []
                        try:
                            _chain = _reg_for_sup.get_chain()
                        except Exception:
                            _chain = []
                        if _chain:
                            _sup_entry = _reg_for_sup.get_entry(_chain[0]["id"])
                    if _sup_entry is not None:
                        _sup_api_key = (
                            _reg_for_sup.resolve_api_key(_sup_entry.id) or "ollama"
                        )
                        # Optional [supervisor].model override (defaults to entry's default_model)
                        _sup_model_override = (_sup_cfg.get("model") or "").strip()
                        _sup_model = _sup_model_override or _sup_entry.model
                        _sup_provider = OpenAICompatibleProvider(
                            base_url=_sup_entry.base_url,
                            api_key=_sup_api_key,
                            model=_sup_model,
                            temperature=0.1,
                            sanitize_inline_cot_dsml=_sanitize_cot_dsml,
                        )
                        logger.info(
                            "supervisor_provider_resolved id=%s base_url=%s model=%s",
                            _sup_entry.id, _sup_entry.base_url, _sup_model,
                        )
            except Exception as _exc:  # noqa: BLE001
                logger.warning(
                    "supervisor_provider_resolve_failed err=%s — falling back to local_llm",
                    str(_exc)[:200],
                )
            if _sup_provider is None:
                # Last-resort fallback: legacy single-provider mode.
                _sup_provider = local_llm
                logger.info(
                    "supervisor_provider_resolved id=legacy base_url=%s — "
                    "registry empty/unavailable; consider adding a provider via Settings",
                    getattr(local_llm, "base_url", "?"),
                )
            # P6 bugfix 2026-05-14 (live-test): auto-mode bypass for
            # supervisor. When permission_gate.auto_mode is ON, the user
            # has delegated "decide for me" semantics — supervisor must
            # NOT block on UI buttons (ask_user). Below callbacks let
            # SupervisorAgent self-drive: check auto state + spawn a
            # follow-up chat task with "<<supervisor_followup>>" trigger.
            def _supervisor_auto_mode_check() -> bool:
                try:
                    return bool(
                        permission_gate_v2 is not None
                        and getattr(permission_gate_v2, "auto_mode", False)
                    )
                except Exception:
                    return False

            async def _supervisor_auto_followup(sid: str, trigger_text: str) -> None:
                # Spawn a chat task with the synthetic trigger text. We
                # look up the most recent control WS for this sid and
                # use the registered re-dispatcher closure (set by chat
                # handler each user turn under `_auto_resume_redispatchers`).
                # If no dispatcher is registered yet, we silently no-op
                # — the queued hint will be consumed when the user (or
                # auto_resume) next pokes the agent.
                _redisp = _auto_resume_redispatchers.get(sid)
                if _redisp is None:
                    logger.info(
                        "supervisor_auto_followup_noop sid=%s reason=no_dispatcher",
                        sid,
                    )
                    return
                _target_ws = _control_connections.get(sid)
                if _target_ws is None:
                    # Fall back to ANY control WS — supervisor alerts are
                    # already broadcast to all of them.
                    for _k, _w in _control_connections.items():
                        _target_ws = _w
                        break
                if _target_ws is None:
                    logger.info(
                        "supervisor_auto_followup_noop sid=%s reason=no_ws",
                        sid,
                    )
                    return
                try:
                    await _redisp(_target_ws, sid)
                except Exception as _ex:
                    logger.warning(
                        "supervisor_auto_followup_dispatch_failed sid=%s err=%s",
                        sid, _ex,
                    )

            _supervisor_agent = _SupAgent(
                provider=_sup_provider,
                snapshot_builder=_snap_builder,
                nudge_queue_push=_push_hint,
                broadcast=_broadcast_supervisor_alert,
                audit=_audit_action,
                auto_mode_check=_supervisor_auto_mode_check,
                auto_followup=_supervisor_auto_followup,
                # P5-S2 G2 (2026-05-12): 30s→120s. supervisor was timing out
                # on deepseek-v4-pro thinking-mode calls — the model takes
                # 30-60s just thinking before emitting the 300-token JSON
                # spec. 120s covers reasoning + transit + chinzy 15s SSE
                # keep-alive. config knob name unchanged for backward compat.
                timeout_seconds=float(_sup_cfg.get("llm_timeout_seconds", 120.0)),
            )
            service_context.register("supervisor", _supervisor_agent)

            # P5-S2 Hook B probe: same logic as the agent_loop probe but
            # captured at a different scope (lifespan vs per-chat). Maps
            # base_sid → code_sid → SessionDB.get_code_todos → filter
            # incomplete. Returned to the watchdog so the (c) trigger
            # rule (idle with todos) can fire even when there's no
            # active chat task.
            # P6 bugfix 2026-05-14 (用户反馈): 长时间未操作时不要骚扰用户。
            # 即使 todos 未完成、agent 状态 idle，也判断用户是否还在用 deskpet
            # （以最近 user 消息时间为准）。30 分钟无 user 活动 → 视为用户
            # 离开，不再主动弹 "Agent seems stuck" 提醒。
            _user_idle_grace_s = float(
                _sup_cfg.get("user_idle_grace_seconds", 1800)
            )

            async def _watchdog_incomplete_todos_probe(_base_sid: str) -> list[dict]:
                try:
                    cm_local = service_context.get("code_mode")
                    if cm_local is None:
                        return []
                    code_sid = cm_local.code_session_id(_base_sid)
                    if not code_sid:
                        return []
                    sdb_local = service_context.get("session_db")
                    if sdb_local is None:
                        return []
                    # P6 bugfix 2026-05-14: check user activity first.
                    # 如果用户 > N 分钟没发消息 → 视为离开，不报警。
                    # 即使 agent 自己拆了 todos，用户没主动让它做 →
                    # 我们也不该自动催 agent 干。
                    try:
                        import time as _t
                        last_user_ts = await sdb_local.last_message_ts(
                            session_id=_base_sid, role="user"
                        ) if hasattr(sdb_local, "last_message_ts") else None
                        if last_user_ts is None:
                            # Fallback: scan messages directly
                            _recent = await sdb_local.get_messages(_base_sid, limit=50)
                            _user_ts = [
                                float(r.get("created_at") or 0)
                                for r in _recent
                                if (r.get("role") or "") == "user"
                            ]
                            last_user_ts = max(_user_ts) if _user_ts else 0.0
                        if last_user_ts <= 0:
                            # No user message ever for this sid → never auto-poke
                            logger.debug(
                                "p6_watchdog_skip_no_user_history sid=%s", _base_sid,
                            )
                            return []
                        user_idle_age = _t.time() - last_user_ts
                        if user_idle_age > _user_idle_grace_s:
                            logger.info(
                                "p6_watchdog_skip_user_away sid=%s user_idle_age=%.0fs grace=%.0fs",
                                _base_sid, user_idle_age, _user_idle_grace_s,
                            )
                            return []
                    except Exception as _ue:  # noqa: BLE001
                        # 探测失败保守起见仍按"用户活跃"处理（行为退化到 pre-fix）
                        logger.debug(
                            "p6_watchdog_user_activity_probe_failed sid=%s err=%s",
                            _base_sid, str(_ue)[:200],
                        )
                    rows = await sdb_local.get_code_todos(code_sid)
                    return [
                        r for r in rows
                        if (r.get("status") or "").lower() not in ("completed", "cancelled")
                    ]
                except Exception as _e:  # noqa: BLE001
                    logger.warning(
                        "p5s2_watchdog_probe_lookup_failed sid=%s err=%s",
                        _base_sid, str(_e)[:200],
                    )
                    return []

            _watchdog = _WatchdogLoop(
                session_activity=service_context.get("session_activity"),
                code_mode_manager=service_context.get("code_mode"),
                hook=_build_sup_hook(_supervisor_agent),
                scan_interval_seconds=float(_sup_cfg.get("scan_interval_seconds", 60)),
                stuck_threshold_seconds=float(_sup_cfg.get("stuck_threshold_seconds", 900)),
                dedup_seconds=float(_sup_cfg.get("dedup_seconds", 720)),
                startup_grace_seconds=float(_sup_cfg.get("startup_grace_seconds", 30)),
                idle_with_todos_threshold_seconds=float(
                    _sup_cfg.get("idle_with_todos_threshold_seconds", 60)
                ),
                incomplete_todos_probe=_watchdog_incomplete_todos_probe,
                # P5-S2 Phase 6 rule (d): proactive death-loop trigger.
                tool_signature_repeat_threshold=int(
                    _sup_cfg.get("tool_signature_repeat_threshold", 3)
                ),
            )
            _watchdog.start()
            service_context.register("watchdog", _watchdog)
            logger.info("p5_supervisor_watchdog_started")

            # P5-S2 Phase 4: AutoResumeOrchestrator — closes the
            # supervisor → main-agent loop. When the chat handler hits
            # max_iterations / circuit_open / permanent_tool_error /
            # hallucination, it forwards to ``orchestrator.handle_failure``;
            # the orchestrator asks supervisor for a hint and (if action
            # is ``nudge``) automatically spawns a fresh chat task on the
            # same sid via the per-sid re-dispatcher closure populated
            # by the chat handler itself.
            try:
                from agent.auto_resume import AutoResumeOrchestrator as _AROrch

                # Dispatcher closure: orchestrator passes (sid, msgs)
                # where ``msgs`` ends in a system msg with the supervisor
                # hint. Production trampoline:
                #   1. Extract hint text from the injected system msg.
                #   2. Push it to nudge_queue (pop_all picks it up at the
                #      top of the next chat task — uniform with P5-S1
                #      injection path).
                #   3. Call the per-sid re-dispatcher (registered by chat
                #      handler each user turn) with the synthetic trigger
                #      ``<<auto_resume>>`` so a fresh AgentLoop runs.
                async def _auto_resume_dispatch(_sid: str, _msgs: list[dict]) -> None:
                    # 1. Pull hint text out of the injected system msg.
                    _hint_text = ""
                    for _m in reversed(_msgs):
                        if _m.get("_is_supervisor_hint"):
                            _content = _m.get("content") or ""
                            if _content.startswith("[Supervisor Hint] "):
                                _hint_text = _content[len("[Supervisor Hint] "):]
                            else:
                                _hint_text = _content
                            break
                    # 2. Push to nudge_queue so the next chat task picks it up.
                    if _hint_text:
                        try:
                            from agent.nudge_queue import Hint as _Hint2
                            _nq2 = service_context.get("nudge_queue")
                            if _nq2 is not None:
                                await _nq2.push(
                                    _sid,
                                    _Hint2(
                                        text=_hint_text,
                                        alert_id="auto_resume",
                                        severity="yellow",
                                    ),
                                )
                        except Exception as _ex:  # noqa: BLE001
                            logger.debug("auto_resume_hint_push_failed sid=%s err=%s", _sid, _ex)
                    # 3. Look up the per-sid re-dispatcher and fire.
                    _redisp = _auto_resume_redispatchers.get(_sid)
                    _ws_for_sid = _control_connections.get(_sid) or _control_connections.get("default")
                    if _redisp is None or _ws_for_sid is None:
                        logger.warning(
                            "auto_resume_no_redispatcher sid=%s ws=%s",
                            _sid, "yes" if _ws_for_sid else "no",
                        )
                        return
                    try:
                        await _redisp(_ws_for_sid, _sid)
                    except Exception as _ex:  # noqa: BLE001
                        logger.warning("auto_resume_redispatch_failed sid=%s err=%s", _sid, _ex)

                # ws emitter — broadcast auto_resume_* events to all control conns.
                async def _auto_resume_emit(_typ: str, _payload: dict) -> None:
                    if not _control_connections:
                        return
                    _msg = {"type": _typ, "payload": _payload}
                    for _sid_key, _ws_obj in list(_control_connections.items()):
                        try:
                            await _ws_obj.send_json(_msg)
                        except Exception as _bex:
                            logger.debug("auto_resume_emit_failed sid=%s err=%s", _sid_key, _bex)

                # Audit writer — bridge to SessionDB.append_supervisor_hint.
                async def _auto_resume_audit(_record: dict) -> None:
                    _sdb = service_context.get("session_db")
                    if _sdb is None:
                        return
                    try:
                        await _sdb.append_supervisor_hint(
                            session_id=_record.get("session_id", ""),
                            alert_id=_record.get("alert_id", ""),
                            hint_text=_record.get("hint_text", ""),
                            action=_record.get("action", "auto_resumed"),
                            severity=_record.get("severity", "yellow"),
                            diagnosis=_record.get("diagnosis", ""),
                        )
                    except Exception as _ex:  # noqa: BLE001
                        logger.debug("auto_resume_audit_failed err=%s", _ex)

                _orch = _AROrch(
                    supervisor=_supervisor_agent,
                    chat_dispatcher=_auto_resume_dispatch,
                    activity_store=service_context.get("session_activity"),
                    max_attempts=int(_sup_cfg.get("max_auto_resume_attempts", 2)),
                    enabled=bool(_sup_cfg.get("auto_resume_enabled", True)),
                    ws_emitter=_auto_resume_emit,
                    audit_writer=_auto_resume_audit,
                )
                service_context.register("auto_resume", _orch)
                logger.info(
                    "p5s2_auto_resume_started enabled=%s max_attempts=%d",
                    _orch._enabled, _orch.max_attempts,
                )
            except Exception as _exc:  # noqa: BLE001
                logger.warning("p5s2_auto_resume_start_failed err=%s", _exc)
        else:
            logger.info("p5_supervisor_disabled_via_config")
    except Exception as exc:  # noqa: BLE001
        logger.warning("p5_supervisor_watchdog_start_failed", error=str(exc))

    # Phase 1.1.5 — 启动落一行 model_context_resolved，让用户/日志一眼
    # 看到当前默认模型解析出的有效窗口 + 来源链。每次 chat 会话另会按
    # session 的实际 model + project_root 再 resolve（resolve() 自带日志）。
    try:
        from llm.model_info import resolve as _resolve_mi

        _startup_v2 = bool(
            ((config.raw.get("context") or {}).get("manager") or {})
            .get("v2_enabled", True)
        )
        if _startup_v2:
            # resolve() 自身落 model_context_resolved INFO 日志。
            _resolve_mi(config.llm.local.model, project_root=None)
        else:
            logger.info(
                "model_context_resolved model=%s window=legacy source=v1_rollback",
                config.llm.local.model,
            )
    except Exception as _mi_exc:  # noqa: BLE001
        logger.warning("model_context_startup_resolve_failed err=%s", _mi_exc)

    logger.info("startup complete")
    yield
    # P5-S1: stop the watchdog cleanly so its task doesn't dangle past
    # shutdown and produce "Task was destroyed but it is pending!" noise.
    _wd = service_context.get("watchdog")
    if _wd is not None:
        try:
            await _wd.stop()
        except Exception as exc:
            logger.warning("p5_supervisor_watchdog_stop_failed", error=str(exc))
    # P4-S15: stop in reverse-dependency order — MCP servers first (so they
    # don't keep firing tool_invoke writes), then VectorWorker (drain
    # outstanding embeds), then SkillLoader's watchdog thread.
    _mcp = service_context.get("mcp_manager")
    if _mcp is not None:
        try:
            await _mcp.stop()
        except Exception as exc:
            logger.warning("p4_mcp_manager_stop_failed", error=str(exc))
    _vw = service_context.get("vector_worker")
    if _vw is not None:
        try:
            await _vw.stop()
        except Exception as exc:
            logger.warning("p4_vector_worker_stop_failed", error=str(exc))
    _iw = service_context.get("image_worker")
    if _iw is not None:
        try:
            await _iw.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("image_worker_stop_failed", error=str(exc))
    _sl = service_context.get("skill_loader")
    if _sl is not None:
        try:
            await _sl.stop()
        except Exception as exc:
            logger.warning("p4_skill_loader_stop_failed", error=str(exc))
    logger.info("shutting down")


app = FastAPI(title="Desktop Pet Backend", version="0.2.0", lifespan=lifespan)

# CORS: Tauri WebView2 runs on tauri://localhost (or https://tauri.localhost).
# fetch() to http://127.0.0.1:8100 is cross-origin and blocked without this.
# WebSocket connections are NOT subject to CORS, only HTTP (POST /config/cloud).
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "tauri://localhost",
        "https://tauri.localhost",
        "http://localhost:5173",   # Vite dev server (browser E2E testing)
        "http://127.0.0.1:5173",
    ],
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type", "X-Shared-Secret"],
)

# Track control channel connections for lip-sync forwarding
_control_connections: dict[str, WebSocket] = {}

# P4-S23: track in-flight chat tasks per session_id so multi-session
# panels can cancel-on-retry without leaking stale AgentLoop runs.
_chat_inflight: dict[str, asyncio.Task] = {}
# P5-S2 Phase 4: per-sid re-dispatchers populated by the chat handler so
# the AutoResumeOrchestrator can spawn a follow-up turn without needing
# direct access to the chat-handler-scoped ``_run_chat`` closure. The
# value is an awaitable ``(ws, sid) -> None`` that re-enters _run_chat
# with a synthetic trigger text. The chat handler refreshes this entry
# on every user-initiated turn so the closure always captures the
# latest local state.
_auto_resume_redispatchers: dict[str, "Callable[[WebSocket, str], Awaitable[None]]"] = {}  # noqa: F821
# Track active voice pipelines by session so that a control-channel `interrupt`
# message can reach the audio-channel pipeline (they are separate WebSockets).
_pipelines: dict[str, "VoicePipeline"] = {}  # noqa: F821 — forward ref, set at runtime


# Opt-in dev mode: set DESKPET_DEV_MODE=1 to bypass shared-secret auth.
# Defaults to strict (secret required) so prod deployments are safe.
DEV_MODE = os.getenv("DESKPET_DEV_MODE", "0") == "1"
if DEV_MODE:
    # Surfaced loudly so a prod deployment accidentally booted with
    # DESKPET_DEV_MODE=1 doesn't silently leak /metrics + WS auth.
    logger.warning(
        "metrics_auth_bypassed_dev_mode",
        note="DESKPET_DEV_MODE=1 — /metrics and WS auth are OPEN. Set DESKPET_DEV_MODE=0 in production.",
    )

def _validate_secret(ws: WebSocket) -> bool:
    if DEV_MODE:
        return True
    secret = ws.headers.get("x-shared-secret", "")
    if not secret:
        secret = ws.query_params.get("secret", "")
    return secrets.compare_digest(secret, SHARED_SECRET)


class CloudConfigRequest(BaseModel):
    base_url: str
    model: str
    api_key: str | None = None   # absent or empty = keep current key
    strategy: str | None = None  # absent = keep current strategy

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, v: str) -> str:
        if not re.match(r'^https?://[^\s]+$', v):
            raise ValueError("base_url must start with http:// or https:// and contain no whitespace")
        return v

    @field_validator("model")
    @classmethod
    def validate_model(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("model must not be empty")
        if "\n" in v:
            raise ValueError("model must not contain newlines")
        if len(v) > 128:
            raise ValueError("model must not exceed 128 characters")
        return v

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str | None) -> str | None:
        # P4-S20-LLM-Unified: 接受短占位符如 "ollama"（本地 Ollama 不验 key）
        # 同时排除常见的 placeholder 值（避免被误当真 key 入库）。
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if len(v) > 256:
            raise ValueError("api_key must not exceed 256 characters")
        # 1-256 字符任意字符串都接受。本地 Ollama "ollama" 6 字符 OK；
        # 真云端 key (sk-..., tsk-..., 几十字符) 也 OK。
        return v

    @field_validator("strategy")
    @classmethod
    def validate_strategy(cls, v: str | None) -> str | None:
        if v is None:
            return None
        valid_values = {s.value for s in RoutingStrategy}
        if v not in valid_values:
            raise ValueError(f"strategy must be one of: {', '.join(sorted(valid_values))}")
        return v


@app.post("/config/cloud")
async def update_cloud_config(body: CloudConfigRequest, request: Request):
    """P4-S20-LLM-Unified: hot-swap the unified LLM provider.

    URL keeps the legacy /config/cloud name for frontend compat, but
    semantics now: replace the single `local_llm` (which serves all
    chat traffic) and persist to llm_runtime.json so the new config
    survives restart.

    Auth: same shared-secret gate as /metrics. DEV_MODE bypasses.
    """
    global _current_cloud_api_key, local_llm

    if not DEV_MODE:
        secret = request.headers.get("x-shared-secret", "")
        if not secret or not secrets.compare_digest(secret, SHARED_SECRET):
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="config"'},
            )

    # Resolve api_key: explicit body value > current in-process key >
    # config.llm.local.api_key (Ollama default "ollama" is OK).
    resolved_key: str | None = body.api_key
    if not resolved_key:
        if _current_cloud_api_key:
            resolved_key = _current_cloud_api_key
        elif local_llm is not None:
            resolved_key = getattr(local_llm, "api_key", None) or "ollama"
        else:
            resolved_key = "ollama"

    current_temperature = 0.7
    if local_llm is not None:
        current_temperature = getattr(local_llm, "temperature", 0.7)

    new_provider = OpenAICompatibleProvider(
        base_url=body.base_url,
        api_key=resolved_key,
        model=body.model,
        temperature=current_temperature,
        sanitize_inline_cot_dsml=_sanitize_cot_dsml,
    )

    # Hot-swap: replace module-level local_llm. The chat handler reads
    # this name fresh each request, so the next chat uses the new
    # provider — no restart needed.
    local_llm = new_provider
    _current_cloud_api_key = resolved_key

    # Persist so next backend restart picks it up. api_key only stored
    # if user explicitly typed one (otherwise stays in keychain via
    # _resolve_cloud_api_key path).
    overrides_to_save: dict = {
        "base_url": body.base_url,
        "model": body.model,
        "temperature": current_temperature,
    }
    if body.api_key and body.api_key.strip():
        # Persist key to runtime json. (Keychain is the more secure path
        # but storing here makes it work without a Tauri shell — useful
        # for headless dev.)
        overrides_to_save["api_key"] = body.api_key.strip()
    _save_llm_runtime_overrides(overrides_to_save)

    # Strategy field deprecated under unified schema — silently accepted
    # but ignored. (HybridRouter still has the API; we just don't drive
    # it any more.)

    logger.info(
        "llm_config_updated base_url=%s model=%s",
        body.base_url, body.model,
        # api_key intentionally NOT logged
    )

    return {
        "ok": True,
        "cloud_configured": True,  # always true under unified schema
        "base_url": body.base_url,
        "model": body.model,
        "has_api_key": bool(resolved_key) and resolved_key != "ollama",
        "strategy": llm._strategy.value,
    }


@app.get("/health")
async def health():
    # P3-S2: surface startup failures (esp. CUDA unavailable / model dir
    # missing) so the Rust supervisor and future frontend banner can
    # react instead of treating a crippled backend as "ready".
    errors = startup_errors.snapshot()
    return {
        "status": "degraded" if errors else "ok",
        "secret_hint": SHARED_SECRET[:4] + "...",
        "strategy": llm._strategy.value,
        "cloud_configured": llm._cloud is not None,
        "startup_errors": errors,
    }


@app.get("/metrics")
async def metrics(request: Request):
    """Prometheus scrape endpoint (P2-1-S6).

    Gated by the same shared secret that protects WS connections. In
    DEV_MODE the gate is open so local `curl` / smoke scripts can hit it
    without juggling headers.
    """
    if not DEV_MODE:
        secret = request.headers.get("x-shared-secret", "")
        if not secret or not secrets.compare_digest(secret, SHARED_SECRET):
            # RFC 7235 §3.1: a 401 MUST carry WWW-Authenticate so clients
            # know which scheme/realm to retry with. Prometheus scrapers
            # and curl both surface the header to the operator.
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="metrics"'},
            )
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)


# --- S14 memory management dispatch -----------------------------------------
# Handled on the control WS (same auth gate as chat/interrupt) so we don't
# expose a second unauthenticated HTTP surface. The four verbs are the minimum
# needed for the "delete-my-data" affordance V5 §6 requires.

async def _handle_memory_message(
    ws: "WebSocket", session_id: str, msg_type: str, payload: dict
) -> None:
    store = service_context.get("memory_store")
    if store is None:
        await ws.send_json({
            "type": "error",
            "payload": {"message": "memory store not registered"},
        })
        return

    try:
        if msg_type == "memory_list":
            # Scope defaults to the current session; ``scope: "all"`` returns
            # every session's turns (export-style). The UI asks per-session.
            scope = payload.get("scope") or "session"
            target_session = None if scope == "all" else payload.get(
                "session_id", session_id
            )
            limit = payload.get("limit")
            turns = await store.list_turns(target_session, limit)
            await ws.send_json({
                "type": "memory_list_response",
                "payload": {
                    "scope": scope,
                    "session_id": target_session,
                    "turns": [
                        {
                            "id": t.id,
                            "session_id": t.session_id,
                            "role": t.role,
                            "content": t.content,
                            "created_at": t.created_at,
                        }
                        for t in turns
                    ],
                },
            })

        elif msg_type == "memory_delete":
            turn_id = payload.get("id")
            if not isinstance(turn_id, int):
                await ws.send_json({
                    "type": "error",
                    "payload": {"message": "memory_delete requires integer id"},
                })
                return
            deleted = await store.delete_turn(turn_id)
            await ws.send_json({
                "type": "memory_delete_ack",
                "payload": {"id": turn_id, "deleted": deleted},
            })

        elif msg_type == "memory_clear":
            scope = payload.get("scope") or "session"
            if scope == "all":
                removed = await store.clear_all()
                await ws.send_json({
                    "type": "memory_clear_ack",
                    "payload": {"scope": "all", "removed": removed},
                })
            else:
                target_session = payload.get("session_id", session_id)
                await store.clear(target_session)
                await ws.send_json({
                    "type": "memory_clear_ack",
                    "payload": {"scope": "session", "session_id": target_session},
                })

        elif msg_type == "memory_export":
            # Dump everything — user asked for their data, they get all of it.
            turns = await store.list_turns(None, None)
            sessions = await store.list_sessions()
            await ws.send_json({
                "type": "memory_export_response",
                "payload": {
                    "exported_at": __import__("time").time(),
                    "sessions": [
                        {
                            "session_id": s.session_id,
                            "turn_count": s.turn_count,
                            "last_message_at": s.last_message_at,
                        }
                        for s in sessions
                    ],
                    "turns": [
                        {
                            "id": t.id,
                            "session_id": t.session_id,
                            "role": t.role,
                            "content": t.content,
                            "created_at": t.created_at,
                        }
                        for t in turns
                    ],
                },
            })
    except AttributeError as exc:
        # Inner store without list_turns/delete_turn/list_sessions/clear_all —
        # surface a clean error instead of a 500 on the wire.
        logger.warning("memory_admin_unsupported", error=str(exc), type=msg_type)
        await ws.send_json({
            "type": "error",
            "payload": {"message": f"{msg_type} not supported by active memory store"},
        })


@app.websocket("/ws/control")
async def control_channel(ws: WebSocket):
    await ws.accept()
    if not _validate_secret(ws):
        try:
            await ws.close(code=4001, reason="invalid secret")
        except Exception:
            pass
        return

    session_id = ws.query_params.get("session_id", "default")
    # P4-S20: gracefully kick the previous holder of this session_id
    # (e.g. an old E2E client) so its disconnect callback doesn't
    # later pop OUR entry. Don't kill ourselves if we're the holder.
    _prev_ws = _control_connections.get(session_id)
    if _prev_ws is not None and _prev_ws is not ws:
        try:
            await _prev_ws.close(code=4002, reason="session replaced")
        except Exception:
            pass
    _control_connections[session_id] = ws
    logger.info("control channel connected", session_id=session_id)
    # P3-S2: first frame after handshake reports startup-error state so the
    # UI can render "CUDA 缺失" / "模型缺失" banners without polling /health.
    try:
        await ws.send_json({
            "type": "startup_status",
            "degraded": startup_errors.is_degraded(),
            "errors": startup_errors.snapshot(),
        })
    except Exception as _e:
        logger.warning("startup_status_send_failed", error=str(_e))
    try:
        while True:
            raw = await ws.receive_json()
            msg_type = raw.get("type", "")

            if msg_type == "ping":
                await ws.send_json({"type": "pong"})

            elif msg_type == "memory_summarize_now":
                # P4-S20-D: 手动触发 — 用户可在 SettingsPanel / MemoryPanel
                # 点 "立即总结老对话" 按钮。参数支持 age_days / min_messages
                # / max_per_run override。
                payload = raw.get("payload", {}) or {}
                if (
                    _summarizer_state_db_path is None
                    or local_llm is None
                ):
                    await ws.send_json({
                        "type": "memory_summarize_response",
                        "payload": {
                            "ok": False,
                            "error": "summarizer not initialized (state_db or local_llm missing)",
                        },
                    })
                    continue
                try:
                    from deskpet.memory.summarizer import (
                        summarize_old_sessions, make_llm_call,
                    )
                    _result = await summarize_old_sessions(
                        db_path=_summarizer_state_db_path,
                        llm_call=make_llm_call(local_llm),
                        age_days=float(payload.get("age_days", 30)),
                        min_messages=int(payload.get("min_messages", 20)),
                        max_per_run=int(payload.get("max_per_run", 10)),
                        vector_worker=service_context.get("vector_worker"),
                    )
                    await ws.send_json({
                        "type": "memory_summarize_response",
                        "payload": {
                            "ok": True,
                            "sessions_scanned": _result.sessions_scanned,
                            "sessions_summarized": _result.sessions_summarized,
                            "messages_archived": _result.messages_archived,
                            "summary_ids": _result.summaries_created,
                            "errors": _result.errors,
                        },
                    })
                except Exception as exc:  # noqa: BLE001
                    logger.warning("memory_summarize_failed: %s", exc)
                    await ws.send_json({
                        "type": "memory_summarize_response",
                        "payload": {"ok": False, "error": str(exc)},
                    })

            elif msg_type == "memory_archive_list":
                # P4-S20-D: 列出 archive 内容供用户查看 / 决定是否恢复或彻底删
                # 参数：limit (default 100), session_id (optional filter)
                payload = raw.get("payload", {}) or {}
                if _summarizer_state_db_path is None:
                    await ws.send_json({
                        "type": "memory_archive_list_response",
                        "payload": {"ok": False, "error": "state_db not initialized"},
                    })
                    continue
                _limit = int(payload.get("limit", 100))
                _filter_sid = payload.get("session_id")
                try:
                    import aiosqlite
                    rows = []
                    async with aiosqlite.connect(_summarizer_state_db_path) as _db:
                        if _filter_sid:
                            cur = await _db.execute(
                                "SELECT id, session_id, role, content, created_at, "
                                "archived_at, archived_into_id "
                                "FROM messages_archive WHERE session_id = ? "
                                "ORDER BY archived_at DESC, created_at DESC LIMIT ?",
                                (_filter_sid, _limit),
                            )
                        else:
                            cur = await _db.execute(
                                "SELECT id, session_id, role, content, created_at, "
                                "archived_at, archived_into_id "
                                "FROM messages_archive "
                                "ORDER BY archived_at DESC, created_at DESC LIMIT ?",
                                (_limit,),
                            )
                        async for r in cur:
                            rows.append({
                                "id": r[0], "session_id": r[1], "role": r[2],
                                "content": r[3], "created_at": r[4],
                                "archived_at": r[5], "archived_into_id": r[6],
                            })
                        await cur.close()
                    await ws.send_json({
                        "type": "memory_archive_list_response",
                        "payload": {"ok": True, "rows": rows, "count": len(rows)},
                    })
                except Exception as exc:  # noqa: BLE001
                    await ws.send_json({
                        "type": "memory_archive_list_response",
                        "payload": {"ok": False, "error": str(exc)},
                    })

            elif msg_type == "permission_response":
                # P4-S20: drain a pending PermissionGate request.
                payload = raw.get("payload", {}) or {}
                rid = payload.get("request_id", "")
                decision = payload.get("decision", "deny")
                fut = _permission_pending.get(rid) if "_permission_pending" in dir() or True else None
                # Note: above expression always reads the module-level
                # name; ``_permission_pending`` is initialized at import.
                fut = _permission_pending.get(rid)
                if fut is not None and not fut.done():
                    try:
                        from deskpet.types.skill_platform import PermissionResponse as _Resp
                        fut.set_result(_Resp(request_id=rid, decision=decision))
                    except Exception as _e:
                        logger.warning("permission_response_set_result_failed", error=str(_e))
                else:
                    logger.info(
                        "permission_response_no_pending",
                        request_id=rid,
                    )

            elif msg_type == "permission_auto_mode_set":
                # P4-S21 #13: toggle "yes-to-all" mode. Settings panel
                # sends {enabled: bool}; we flip the flag on the live
                # PermissionGate instance. Default is OFF — has to be
                # explicitly opted in. Reverts on backend restart.
                payload = raw.get("payload", {}) or {}
                enabled = bool(payload.get("enabled", False))
                if permission_gate_v2 is not None:
                    # P4-S25: route through set_auto_mode so the choice
                    # persists across backend restart (was process-only).
                    permission_gate_v2.set_auto_mode(enabled)
                    logger.info(
                        "permission_auto_mode_set", enabled=enabled,
                    )
                await ws.send_json({
                    "type": "permission_auto_mode_response",
                    "payload": {"enabled": enabled},
                })

            elif msg_type == "plugin_list":
                # P4-S20 Stage D — list all discovered plugins with enabled status.
                if plugin_manager is None:
                    await ws.send_json({
                        "type": "plugin_list_response",
                        "payload": {"plugins": [], "error": "plugin manager not initialized"},
                    })
                    continue
                await ws.send_json({
                    "type": "plugin_list_response",
                    "payload": {"plugins": plugin_manager.list_plugins()},
                })

            elif msg_type == "plugin_enable":
                payload = raw.get("payload", {}) or {}
                name = payload.get("name", "")
                if plugin_manager is None or not plugin_manager.enable(name):
                    await ws.send_json({
                        "type": "plugin_enable_response",
                        "payload": {"ok": False, "name": name, "error": "unknown plugin"},
                    })
                    continue
                # Best-effort SkillLoader hot-reload so the plugin's skills appear.
                try:
                    loader = service_context.get("skill_loader")
                    if loader is not None and hasattr(loader, "reload"):
                        loader.reload()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("skill_loader_reload_failed", error=str(exc))
                await ws.send_json({
                    "type": "plugin_enable_response",
                    "payload": {"ok": True, "name": name},
                })

            elif msg_type == "plugin_disable":
                payload = raw.get("payload", {}) or {}
                name = payload.get("name", "")
                if plugin_manager is None:
                    await ws.send_json({
                        "type": "plugin_disable_response",
                        "payload": {"ok": False, "name": name, "error": "plugin manager not initialized"},
                    })
                    continue
                plugin_manager.disable(name)
                try:
                    loader = service_context.get("skill_loader")
                    if loader is not None and hasattr(loader, "reload"):
                        loader.reload()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("skill_loader_reload_failed", error=str(exc))
                await ws.send_json({
                    "type": "plugin_disable_response",
                    "payload": {"ok": True, "name": name},
                })

            elif msg_type == "skill_marketplace_list":
                # P4-S20 Stage C — fetch + cache the official registry.
                if skill_registry_client is None:
                    await ws.send_json({
                        "type": "skill_marketplace_list_response",
                        "payload": {"skills": [], "error": "marketplace not initialized"},
                    })
                    continue
                _data = await skill_registry_client.fetch()
                await ws.send_json({
                    "type": "skill_marketplace_list_response",
                    "payload": _data,
                })

            elif msg_type == "skill_list_installed":
                # P4-S20 Stage C — list user-installed skills.
                if skill_installer is None:
                    await ws.send_json({
                        "type": "skill_list_installed_response",
                        "payload": {"skills": [], "error": "marketplace not initialized"},
                    })
                    continue
                _installed = []
                for sk in skill_installer.skills_dir.iterdir():
                    if not sk.is_dir():
                        continue
                    sm = sk / "SKILL.md"
                    if not sm.exists():
                        continue
                    try:
                        from deskpet.skills.parser import parse_skill_md
                        meta = parse_skill_md(sm)
                        _installed.append({
                            "name": meta.name,
                            "description": meta.description,
                            "version": meta.version,
                            "path": str(sk),
                            "allowed_tools": list(meta.allowed_tools),
                        })
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("skill_list_parse_failed", path=str(sm), error=str(exc))
                await ws.send_json({
                    "type": "skill_list_installed_response",
                    "payload": {"skills": _installed},
                })

            elif msg_type == "skill_install_from_url":
                # P4-S20 Stage C — stage clone, return manifest for confirm.
                payload = raw.get("payload", {}) or {}
                url = payload.get("url", "")
                if skill_installer is None:
                    await ws.send_json({
                        "type": "skill_install_pending",
                        "payload": {"ok": False, "error": "marketplace not initialized"},
                    })
                    continue
                try:
                    staged = await skill_installer.stage(url)
                    _skill_staged[staged.staging_id] = staged
                    await ws.send_json({
                        "type": "skill_install_pending",
                        "payload": {
                            "ok": True,
                            "staging_id": staged.staging_id,
                            "name": staged.name,
                            "manifest": staged.manifest,
                            "permission_categories": list(staged.permission_categories),
                        },
                    })
                except Exception as exc:  # noqa: BLE001
                    await ws.send_json({
                        "type": "skill_install_pending",
                        "payload": {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                    })

            elif msg_type == "skill_install_confirm":
                # P4-S20 Stage C — finalize or cancel a staged install.
                payload = raw.get("payload", {}) or {}
                staging_id = payload.get("staging_id", "")
                approve = bool(payload.get("approve", False))
                if skill_installer is None or staging_id not in _skill_staged:
                    await ws.send_json({
                        "type": "skill_install_confirm_response",
                        "payload": {"ok": False, "error": "no such staging_id"},
                    })
                    continue
                staged = _skill_staged.pop(staging_id)
                if not approve:
                    skill_installer.cancel(staged)
                    await ws.send_json({
                        "type": "skill_install_confirm_response",
                        "payload": {"ok": False, "reason": "user denied"},
                    })
                    continue
                try:
                    final_path = skill_installer.finalize(staged)
                    # Trigger SkillLoader hot-reload best-effort
                    try:
                        loader = service_context.get("skill_loader")
                        if loader is not None and hasattr(loader, "reload"):
                            loader.reload()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("skill_loader_reload_failed", error=str(exc))
                    await ws.send_json({
                        "type": "skill_install_confirm_response",
                        "payload": {
                            "ok": True,
                            "name": staged.name,
                            "path": str(final_path),
                        },
                    })
                except Exception as exc:  # noqa: BLE001
                    await ws.send_json({
                        "type": "skill_install_confirm_response",
                        "payload": {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                    })

            elif msg_type == "skill_uninstall":
                payload = raw.get("payload", {}) or {}
                name = payload.get("name", "")
                if skill_installer is None:
                    await ws.send_json({
                        "type": "skill_uninstall_response",
                        "payload": {"ok": False, "error": "marketplace not initialized"},
                    })
                    continue
                try:
                    skill_installer.uninstall(name)
                    try:
                        loader = service_context.get("skill_loader")
                        if loader is not None and hasattr(loader, "reload"):
                            loader.reload()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("skill_loader_reload_failed", error=str(exc))
                    await ws.send_json({
                        "type": "skill_uninstall_response",
                        "payload": {"ok": True, "name": name},
                    })
                except Exception as exc:  # noqa: BLE001
                    await ws.send_json({
                        "type": "skill_uninstall_response",
                        "payload": {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                    })

            elif msg_type == "code_mode_enter":
                # P4-S22: enter Code mode for this session.
                # Payload: {project_path?, suggested_name?, session_id?}
                # P4-S23: payload.session_id lets the new code panel
                # bind a fresh base sid per project, so multiple Code
                # mode sessions can run in parallel without colliding
                # on the WS-level "default" session.
                payload = raw.get("payload", {}) or {}
                project_path = payload.get("project_path") or None
                suggested_name = (
                    payload.get("suggested_name") or "untitled"
                )
                target_sid = payload.get("session_id") or session_id
                cmm = service_context.get("code_mode")
                if cmm is None:
                    await ws.send_json({
                        "type": "code_mode_state",
                        "payload": {
                            "enabled": False,
                            "error": "code mode manager not initialized",
                            "session_id": target_sid,
                        },
                    })
                    continue
                try:
                    from deskpet.code_mode import resolve_project_root
                    root = resolve_project_root(project_path, suggested_name)
                    # P4-S25 B4 fix: if this project_root already has a
                    # persisted base_session_id, REUSE it. Otherwise the
                    # frontend's freshly-generated random sid creates a
                    # new conversation slot and orphans all prior chat
                    # history (which is keyed by base_session_id in the
                    # messages table). Lookup is by absolute path —
                    # resolve_project_root already canonicalised it.
                    sdb = service_context.get("session_db")
                    if sdb is not None:
                        try:
                            existing = await sdb.list_code_sessions()
                            target_root = str(root.resolve())
                            for row in existing:
                                if str(row["project_root"]) == target_root:
                                    target_sid = row["base_session_id"]
                                    logger.info(
                                        "code_mode_enter_reusing_existing",
                                        base_session_id=target_sid,
                                        project_root=target_root,
                                    )
                                    break
                        except Exception as exc:  # noqa: BLE001
                            logger.debug(
                                "code_mode_enter_lookup_failed",
                                error=str(exc),
                            )
                    state = cmm.enter(target_sid, root)
                    await ws.send_json({
                        "type": "code_mode_state",
                        "payload": {
                            "enabled": True,
                            "project_root": str(state.project_root),
                            "project_name": state.project_name,
                            "code_session_id": state.code_session_id,
                            "session_id": target_sid,
                        },
                    })
                except Exception as exc:  # noqa: BLE001
                    logger.warning("code_mode_enter_failed", error=str(exc))
                    await ws.send_json({
                        "type": "code_mode_state",
                        "payload": {
                            "enabled": False,
                            "error": str(exc),
                            "session_id": target_sid,
                        },
                    })

            elif msg_type == "code_mode_exit":
                payload = raw.get("payload", {}) or {}
                target_sid = payload.get("session_id") or session_id
                cmm = service_context.get("code_mode")
                if cmm is not None:
                    cmm.exit(target_sid)
                # Clear injected project_root from tool registry context
                if deskpet_tool_registry_v2 is not None:
                    deskpet_tool_registry_v2.set_session_context(target_sid, None)
                # P5-S1: clear supervisor state (activity tracking, queued nudges)
                # before broadcasting the exit so any in-flight watchdog scan
                # sees a consistent "no longer in Code mode" view.
                _sa = service_context.get("session_activity")
                if _sa is not None:
                    try:
                        await _sa.drop(target_sid)
                    except Exception as _exc:
                        logger.debug("session_activity_drop_failed", error=str(_exc))
                _nq = service_context.get("nudge_queue")
                if _nq is not None:
                    try:
                        await _nq.clear(target_sid)
                    except Exception as _exc:
                        logger.debug("nudge_queue_clear_failed", error=str(_exc))
                await ws.send_json({
                    "type": "code_mode_state",
                    "payload": {"enabled": False, "session_id": target_sid},
                })

            elif msg_type == "code_mode_status":
                payload = raw.get("payload", {}) or {}
                target_sid = payload.get("session_id") or session_id
                cmm = service_context.get("code_mode")
                if cmm is None or not cmm.is_enabled(target_sid):
                    await ws.send_json({
                        "type": "code_mode_state",
                        "payload": {"enabled": False, "session_id": target_sid},
                    })
                else:
                    s = cmm.get(target_sid)
                    await ws.send_json({
                        "type": "code_mode_state",
                        "payload": {
                            "enabled": True,
                            "project_root": str(s.project_root) if s else None,
                            "project_name": s.project_name if s else "",
                            "code_session_id": s.code_session_id if s else None,
                            "session_id": target_sid,
                        },
                    })

            elif msg_type == "code_todo_list":
                # P4-S22: frontend asks for current todos (e.g. on panel open).
                payload = raw.get("payload", {}) or {}
                target_sid = payload.get("session_id") or session_id
                cmm = service_context.get("code_mode")
                sdb = service_context.get("session_db")
                items: list = []
                if cmm and sdb:
                    csid = cmm.code_session_id(target_sid)
                    if csid:
                        try:
                            items = await sdb.get_code_todos(csid)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "code_todo_list_failed", error=str(exc),
                            )
                await ws.send_json({
                    "type": "code_todo_update",
                    "payload": {"items": items, "session_id": target_sid},
                })

            elif msg_type == "session_messages_load":
                # P4-S23: panel reload (F5) needs to rehydrate chat
                # history from SessionDB. Returns messages for the
                # given session_id (default: ws session_id).
                # Capped at 200 msgs/session to keep payload sane —
                # users can scroll older messages via a future
                # paginated load if needed.
                payload = raw.get("payload", {}) or {}
                target_sid = payload.get("session_id") or session_id
                limit = int(payload.get("limit") or 200)
                sdb = service_context.get("session_db")
                msgs: list = []
                if sdb is not None:
                    try:
                        rows = await sdb.get_messages(target_sid, limit=limit)
                        # P6 bugfix 2026-05-14: 历史还原 — 之前过滤了
                        # role not in (user,assistant) 和 empty content,
                        # 导致 tool_call (role='assistant' content=''
                        # tool_calls=[...]) 和 tool_result (role='tool')
                        # 全部被丢，UI 重启后只剩 user 气泡。现在全部
                        # 返回，前端 ws.ts 决定怎么渲染。
                        msgs = []
                        for r in rows:
                            _row_role = r.get("role") or ""
                            if _row_role not in ("user", "assistant", "tool"):
                                continue  # 仍跳过 system 等
                            _entry: dict[str, Any] = {
                                "id": str(r.get("id") or ""),
                                "role": _row_role,
                                "text": r.get("content") or "",
                                "ts": float(r.get("created_at") or 0) * 1000,
                            }
                            # tool_calls JSON (assistant 行调工具时)
                            _tcs_raw = r.get("tool_calls")
                            if _tcs_raw:
                                try:
                                    import json as _load_json
                                    _entry["tool_calls"] = (
                                        _load_json.loads(_tcs_raw)
                                        if isinstance(_tcs_raw, str) else _tcs_raw
                                    )
                                except Exception:
                                    pass
                            # tool_call_id (tool 行的回指)
                            _tcid = r.get("tool_call_id")
                            if _tcid:
                                _entry["tool_call_id"] = _tcid
                            msgs.append(_entry)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "session_messages_load_failed",
                            error=str(exc), session_id=target_sid,
                        )
                await ws.send_json({
                    "type": "session_messages_response",
                    "payload": {"session_id": target_sid, "messages": msgs},
                })

            elif msg_type == "chat_v2_interrupt":
                # P4-S25 B3: stop the in-flight chat task for a given
                # session WITHOUT enqueueing a new one. Pairs with the
                # frontend "停止" button — when the user clicks it, the
                # current LLM call gets cancelled and the inflight
                # state on that session clears, freeing the user to
                # type a fresh prompt.
                payload = raw.get("payload", {}) or {}
                target_sid = payload.get("session_id") or session_id
                _t = _chat_inflight.get(target_sid)
                cancelled = False
                if _t is not None and not _t.done():
                    _t.cancel()
                    cancelled = True
                # Tell the frontend regardless — it expects a response so
                # the button can revert. If nothing was running, send the
                # confirmation anyway (idempotent UX).
                await ws.send_json({
                    "type": "chat_v2_interrupted",
                    "payload": {
                        "session_id": target_sid,
                        "cancelled": cancelled,
                    },
                })

            elif msg_type == "code_session_delete":
                # P4-S24 followup: user clicked 🗑️ on a project tile / sidebar
                # entry and confirmed the dialog. Drop the in-memory state
                # AND wipe code_todos for that code session. Chat history
                # in `messages` is preserved on purpose (same philosophy as
                # `code_mode_exit`) so re-adding the same project root
                # later resumes from where the user left off.
                payload = raw.get("payload", {}) or {}
                target_sid = payload.get("session_id") or session_id
                cmm = service_context.get("code_mode")
                sdb = service_context.get("session_db")
                deleted_csid: str | None = None
                if cmm is not None:
                    # P4-S25 B4: cmm.delete drops in-memory state AND
                    # the code_sessions persistence row, so the project
                    # truly disappears across restart. (Old cmm.exit
                    # only cleared memory; would resurrect on next boot.)
                    deleted_csid = await cmm.delete(target_sid)
                # Clear injected project_root from tool registry context
                if deskpet_tool_registry_v2 is not None:
                    deskpet_tool_registry_v2.set_session_context(target_sid, None)
                deleted_todos = 0
                if sdb is not None and deleted_csid:
                    try:
                        deleted_todos = await sdb.delete_code_todos(deleted_csid)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "code_session_delete_todos_failed",
                            error=str(exc),
                            session_id=deleted_csid,
                        )
                await ws.send_json({
                    "type": "code_session_deleted",
                    "payload": {
                        "base_session_id": target_sid,
                        "code_session_id": deleted_csid,
                        "deleted_todos": deleted_todos,
                    },
                })
                # Auto-broadcast a refreshed list so multi-window
                # dashboards (pet + code panel) both update without
                # the frontend having to ask.
                items_list_after: list = []
                if cmm is not None:
                    for base_sid_after, st_after in cmm.all_sessions().items():
                        todo_count_after = 0
                        if sdb is not None and st_after.code_session_id:
                            try:
                                tds = await sdb.get_code_todos(st_after.code_session_id)
                                todo_count_after = len(tds)
                            except Exception:
                                pass
                        items_list_after.append({
                            "base_session_id": base_sid_after,
                            "code_session_id": st_after.code_session_id,
                            "project_root": str(st_after.project_root) if st_after.project_root else None,
                            "project_name": st_after.project_name,
                            "todo_count": todo_count_after,
                            "enabled": st_after.enabled,
                        })
                await ws.send_json({
                    "type": "code_sessions_list_response",
                    "payload": {"items": items_list_after},
                })

            elif msg_type == "code_sessions_list":
                # P4-S23: dashboard pulls all enabled code sessions in
                # one shot. Returns [{base_session_id, code_session_id,
                # project_root, project_name, todo_count, enabled}].
                cmm = service_context.get("code_mode")
                sdb = service_context.get("session_db")
                items_list: list = []
                if cmm is not None:
                    for base_sid, st in cmm.all_sessions().items():
                        todo_count = 0
                        if sdb is not None and st.code_session_id:
                            try:
                                todos = await sdb.get_code_todos(st.code_session_id)
                                todo_count = len(todos)
                            except Exception:
                                pass
                        items_list.append({
                            "base_session_id": base_sid,
                            "code_session_id": st.code_session_id,
                            "project_root": str(st.project_root) if st.project_root else None,
                            "project_name": st.project_name,
                            "todo_count": todo_count,
                            "enabled": st.enabled,
                        })
                await ws.send_json({
                    "type": "code_sessions_list_response",
                    "payload": {"items": items_list},
                })

            elif msg_type in (
                "settings_providers_list_request",
                "settings_providers_add",
                "settings_providers_update",
                "settings_providers_remove",
                "settings_providers_reorder",
            ):
                # P5-S2 multi-provider-management Phase 2:
                # CRUD + reorder against LLMProviderRegistry. Mutations
                # broadcast a `providers_changed` event to ALL control
                # connections so multi-window UIs (pet panel + code panel)
                # stay in sync without polling.
                #
                # Spec: openspec/changes/multi-provider-management/specs/
                #       frontend-ipc-surface/spec.md
                _payload = raw.get("payload", {}) or {}
                _reg = service_context.get("provider_registry")
                if _reg is None:
                    await ws.send_json({
                        "type": "settings_providers_error",
                        "payload": {
                            "reason": "registry_unavailable",
                            "detail": "provider_registry not initialized",
                        },
                    })
                    continue

                async def _broadcast_providers_changed() -> None:
                    """Fan out the new provider list to every open control
                    ws. Mirrors the pattern used by `_todo_broadcaster` /
                    supervisor alert broadcaster."""
                    snapshot = {
                        "type": "providers_changed",
                        "payload": {"providers": _reg.list_providers()},
                    }
                    if not _control_connections:
                        return
                    for _sid_key, _ws_obj in list(_control_connections.items()):
                        try:
                            await _ws_obj.send_json(snapshot)
                        except Exception as _bex:  # noqa: BLE001
                            logger.debug(
                                "providers_changed_broadcast_failed sid=%s err=%s",
                                _sid_key, _bex,
                            )

                if msg_type == "settings_providers_list_request":
                    await ws.send_json({
                        "type": "settings_providers_list_response",
                        "payload": {"providers": _reg.list_providers()},
                    })

                elif msg_type == "settings_providers_add":
                    try:
                        entry = await _reg.add_provider(_payload)
                    except ValueError as exc:
                        # Classify error: duplicate vs missing_field vs
                        # invalid_id. The registry raises a single
                        # ValueError for all of these; we sniff the text.
                        _msg = str(exc)
                        if "already exists" in _msg:
                            _reason = "duplicate_id"
                        elif "missing required fields" in _msg or "api_key is required" in _msg:
                            _reason = "missing_field"
                        elif "invalid provider id" in _msg:
                            _reason = "invalid_id"
                        else:
                            _reason = "invalid_payload"
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {"reason": _reason, "detail": _msg},
                        })
                    except Exception as exc:  # noqa: BLE001
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {"reason": "internal_error", "detail": str(exc)},
                        })
                    else:
                        await ws.send_json({
                            "type": "settings_providers_added",
                            "payload": {"provider": entry.to_public_dict()},
                        })
                        await _broadcast_providers_changed()

                elif msg_type == "settings_providers_update":
                    _pid = _payload.get("id")
                    _patch = _payload.get("patch", {}) or {}
                    if not _pid:
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {"reason": "missing_field", "detail": "id required"},
                        })
                        continue
                    try:
                        entry = await _reg.update_provider(_pid, **_patch)
                    except KeyError:
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {"reason": "not_found", "detail": f"provider {_pid!r} not found"},
                        })
                    except ValueError as exc:
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {"reason": "invalid_payload", "detail": str(exc)},
                        })
                    except Exception as exc:  # noqa: BLE001
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {"reason": "internal_error", "detail": str(exc)},
                        })
                    else:
                        await ws.send_json({
                            "type": "settings_providers_updated",
                            "payload": {"provider": entry.to_public_dict()},
                        })
                        await _broadcast_providers_changed()

                elif msg_type == "settings_providers_remove":
                    _pid = _payload.get("id")
                    if not _pid:
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {"reason": "missing_field", "detail": "id required"},
                        })
                        continue
                    try:
                        await _reg.remove_provider(_pid)
                    except KeyError:
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {"reason": "not_found", "detail": f"provider {_pid!r} not found"},
                        })
                    except Exception as exc:  # noqa: BLE001
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {"reason": "internal_error", "detail": str(exc)},
                        })
                    else:
                        # Clean up orphan code_session_provider bindings
                        # so resolution doesn't surface a stale pin.
                        _sdb_clean = service_context.get("session_db")
                        if _sdb_clean is not None:
                            try:
                                await _sdb_clean.clear_bindings_for_provider(_pid)
                            except Exception as exc:  # noqa: BLE001
                                logger.warning(
                                    "clear_bindings_for_provider_failed pid=%s err=%s",
                                    _pid, exc,
                                )
                        await ws.send_json({
                            "type": "settings_providers_removed",
                            "payload": {"id": _pid},
                        })
                        await _broadcast_providers_changed()

                elif msg_type == "settings_providers_reorder":
                    _ordered = _payload.get("ordered_ids") or []
                    try:
                        await _reg.reorder(list(_ordered))
                    except ValueError as exc:
                        _msg = str(exc)
                        # Compute the missing-id detail for nicer UX.
                        _have = {p["id"] for p in _reg.list_providers()}
                        _missing = sorted(_have - set(_ordered))
                        _detail = f"missing: {', '.join(_missing)}" if _missing else _msg
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {"reason": "incomplete_order", "detail": _detail},
                        })
                    except Exception as exc:  # noqa: BLE001
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {"reason": "internal_error", "detail": str(exc)},
                        })
                    else:
                        await ws.send_json({
                            "type": "settings_providers_reordered",
                            "payload": {"providers": _reg.list_providers()},
                        })
                        await _broadcast_providers_changed()

            elif msg_type == "settings_providers_probe_models":
                # P5-S2 v2: GET <base_url>/models for an OpenAI-compat endpoint
                # to discover available models. Used by AddProviderModal "🔍
                # auto-fetch" so users don't have to type model names by hand.
                # Reply: { ok, models: [string], detail? }
                _payload = raw.get("payload", {}) or {}
                _base_url = str(_payload.get("base_url") or "").rstrip("/")
                _api_key = str(_payload.get("api_key") or "")
                if not _base_url:
                    await ws.send_json({
                        "type": "settings_providers_probe_models_response",
                        "payload": {
                            "ok": False,
                            "models": [],
                            "detail": "base_url required",
                        },
                    })
                    continue
                try:
                    import httpx as _httpx
                    _models_url = f"{_base_url}/models"
                    _headers = {"Accept": "application/json"}
                    if _api_key and _api_key.lower() != "ollama":
                        _headers["Authorization"] = f"Bearer {_api_key}"
                    async with _httpx.AsyncClient(timeout=15.0) as _hc:
                        _resp = await _hc.get(_models_url, headers=_headers)
                    if _resp.status_code != 200:
                        await ws.send_json({
                            "type": "settings_providers_probe_models_response",
                            "payload": {
                                "ok": False,
                                "models": [],
                                "detail": f"HTTP {_resp.status_code}: {_resp.text[:200]}",
                            },
                        })
                    else:
                        _body = _resp.json()
                        # OpenAI-format: {"data": [{"id": "..."}, ...]}
                        # Ollama-format: {"models": [{"name": "..."}, ...]}
                        # tolerate either.
                        _items = _body.get("data") if isinstance(_body, dict) else None
                        if not isinstance(_items, list):
                            _items = _body.get("models") if isinstance(_body, dict) else None
                        if not isinstance(_items, list):
                            _items = []
                        _names: list[str] = []
                        for _it in _items:
                            if isinstance(_it, dict):
                                _n = _it.get("id") or _it.get("name") or _it.get("model")
                                if _n:
                                    _names.append(str(_n))
                            elif isinstance(_it, str):
                                _names.append(_it)
                        await ws.send_json({
                            "type": "settings_providers_probe_models_response",
                            "payload": {
                                "ok": True,
                                "models": _names,
                            },
                        })
                except Exception as _probe_exc:  # noqa: BLE001
                    await ws.send_json({
                        "type": "settings_providers_probe_models_response",
                        "payload": {
                            "ok": False,
                            "models": [],
                            "detail": str(_probe_exc)[:300],
                        },
                    })

            elif msg_type in ("code_session_set_provider", "code_session_set_model"):
                # P5-S2 multi-provider-management Phase 2:
                # Per-session override binding. set_provider rewrites the
                # provider_id (preserving preferred_model); set_model
                # rewrites preferred_model alone (preserving provider_id,
                # which may stay null = "still global chain"). Both
                # respond with the full resolved binding so the frontend
                # can update its session card without a separate fetch.
                _payload = raw.get("payload", {}) or {}
                _sid_target = _payload.get("session_id")
                _sdb_bind = service_context.get("session_db")
                if not _sid_target or _sdb_bind is None:
                    await ws.send_json({
                        "type": "settings_providers_error",
                        "payload": {
                            "reason": "missing_field",
                            "detail": "session_id required + session_db must be initialized",
                        },
                    })
                    continue

                current = await _sdb_bind.get_code_session_provider_binding(_sid_target)
                if msg_type == "code_session_set_provider":
                    new_pid = _payload.get("provider_id")  # may be None
                    new_model = current.get("preferred_model")
                    # provider-only change preserves existing model_params.
                    new_params = current.get("model_params")
                    out_type = "code_session_provider_set"
                else:
                    new_pid = current.get("provider_id")  # preserve
                    new_model = _payload.get("model")  # may be None
                    # code-session-model-params: Cursor picker sends
                    # `params`; legacy `{session_id,model}` (no `params`
                    # key) ⇒ provider defaults (None), per spec
                    # "Back-compat IPC". Must be a dict or None.
                    _raw_params = _payload.get("params")
                    new_params = _raw_params if isinstance(_raw_params, dict) else None
                    out_type = "code_session_model_set"

                try:
                    await _sdb_bind.set_code_session_provider_binding(
                        _sid_target, new_pid, new_model, new_params,
                    )
                except Exception as exc:  # noqa: BLE001
                    await ws.send_json({
                        "type": "settings_providers_error",
                        "payload": {"reason": "internal_error", "detail": str(exc)},
                    })
                else:
                    await ws.send_json({
                        "type": out_type,
                        "payload": {
                            "session_id": _sid_target,
                            "provider_id": new_pid,
                            "preferred_model": new_model,
                            "model_params": new_params,
                        },
                    })

            elif msg_type in ("chat", "chat_v2"):
                # P4-S20-LLM-Unified: 单一对话路径。
                # 永远走 AgentLoop tool_use loop（即原 chat_v2 路径），
                # 同时保留 ContextAssembler 长期记忆召回 + BillingLedger
                # 计费（继承自原 chat 路径）。
                #
                # CRITICAL: run in a background task so the WS recv
                # loop keeps draining permission_response messages —
                # gate's responder Future never gets set otherwise.
                #
                # `chat_v2` 类型保留作为别名，前端无感升级。
                # P4-S23: payload may carry an explicit `session_id` —
                # the new code-panel can talk to multiple code sessions
                # over one shared WS, so we honor whatever the client
                # asks for and fall back to the WS-level session_id
                # (which is "default" for the pet's chat).
                _payload = raw.get("payload", {}) or {}
                text = _payload.get("text", "")
                _msg_sid = _payload.get("session_id") or session_id
                if (
                    deskpet_tool_registry_v2 is None
                    or permission_gate_v2 is None
                ):
                    await ws.send_json({
                        "type": "chat_v2_error",
                        "payload": {"error": "v2 stack not initialized", "session_id": _msg_sid},
                    })
                    continue

                # P4-S22: auto-suggest Code mode when user looks like
                # they're starting a project, and code mode isn't on yet.
                # We send a one-shot banner — the actual chat still runs
                # below as a normal companion turn (we don't hijack).
                try:
                    from deskpet.code_mode import maybe_suggest_code_mode
                    cmm = service_context.get("code_mode")
                    in_code = bool(cmm and cmm.is_enabled(_msg_sid))
                    if not in_code and maybe_suggest_code_mode(text):
                        await ws.send_json({
                            "type": "code_mode_suggest",
                            "payload": {
                                "trigger_text": text[:120],
                                "reason": "detected project intent",
                                "session_id": _msg_sid,
                            },
                        })
                except Exception as _exc:  # noqa: BLE001
                    logger.debug("code_mode_suggest_failed", error=str(_exc))

                # P5-S2 Phase 4: a fresh user-initiated chat message means
                # the user has implicitly granted a new auto-resume budget.
                # Reset the attempts counter so subsequent failures may
                # auto-resume up to ``max_attempts`` times again. Skipped
                # for synthetic re-entry texts (`<<supervisor_followup>>`,
                # `<<auto_resume>>`) so they consume the existing budget.
                if not (text or "").startswith("<<"):
                    _sa_for_reset = service_context.get("session_activity")
                    if _sa_for_reset is not None:
                        try:
                            await _sa_for_reset.reset_auto_resume_attempts(_msg_sid)
                        except Exception as _ex:  # noqa: BLE001
                            logger.debug("auto_resume_reset_failed sid=%s err=%s", _msg_sid, _ex)

                async def _run_chat(_ws, _text, _sid):
                    # P4-S20-LLM-Unified-fix: 持久化用户消息到 SessionDB
                    # 并入向量库。老 chat 路径靠 SimpleLLMAgent.chat_stream
                    # 内部写入；新路径直接调 AgentLoop 绕过了 SimpleLLMAgent，
                    # 这里必须显式补回，否则下次召回什么都没。
                    _mm = service_context.get("memory_manager")
                    _vw = service_context.get("vector_worker")
                    _sdb = service_context.get("session_db")
                    _user_msg_id: int | None = None
                    # P6 bugfix 2026-05-13 (UI visible bug): 不要把 supervisor /
                    # auto-resume 用的 sentinel 文本 (`<<auto_resume>>`,
                    # `<<supervisor_followup>>`) 当用户消息写进 SessionDB —— 否则
                    # code panel 会把它们渲染成 "You: <<auto_resume>>" 让用户困惑。
                    # 这些 sentinel 只是触发 token，真正的 hint 走另外的 system msg
                    # 注入路径，pop_hint() 会把上下文喂给 LLM。
                    _is_sentinel = (_text or "").startswith("<<") and (_text or "").endswith(">>")
                    if _sdb is not None and not _is_sentinel:
                        try:
                            _user_msg_id = await _sdb.append_message(
                                session_id=_sid, role="user", content=_text,
                            )
                            if _vw is not None and _user_msg_id is not None:
                                await _vw.enqueue(_user_msg_id, _text)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "chat_persist_user_failed", error=str(exc),
                            )

                    # OpenSpec 2026-05-16 §D2 — capability gate（防漂移）。
                    # 在进 ContextAssembler / agent loop 前拦下"明显需要
                    # deskpet 没有的能力"的请求（图像/视频/语音/作曲/3D
                    # 生成），直接 graceful refuse + 替代建议，杜绝"无法
                    # 完成 → 漂移到记忆里的旧项目"（2026-05-16 实测 bug：
                    # default session 说"生成海报图片" → agent 建了 17 个
                    # VPN CLI 文件）。这不是沙箱/弹窗，是诚实直接答复。
                    # sentinel（<<auto_resume>> 等）不过门；available_tools
                    # 实时从 ToolRegistry 取，新增图像工具自动放行。
                    if not _is_sentinel:
                        try:
                            _cap_cfg = (config.raw.get("companion") or {})
                            _cap_enabled = bool(
                                _cap_cfg.get("capability_gate_enabled", True)
                            )
                            from agent.capability_gate import (
                                classify_request as _cap_classify,
                            )
                            _avail_tools = (
                                deskpet_tool_registry_v2.list_tools()
                                if deskpet_tool_registry_v2 is not None
                                else []
                            )
                            # 歧义兜底用的 haiku-class LLM：复用统一
                            # provider 包一层 tool_use_shim（与 agent
                            # loop 同 endpoint）。构造失败 → None →
                            # 跳过兜底默认放行（不冤枉正常请求）。
                            _gate_llm = None
                            try:
                                from agent.tool_use_shim import (
                                    OpenAICompatibleAgentLLM as _GateShim,
                                )
                                _gate_provider = local_llm or cloud_llm
                                if _gate_provider is not None:
                                    _gate_llm = _GateShim(provider=_gate_provider)
                            except Exception:  # noqa: BLE001
                                _gate_llm = None
                            _verdict = await _cap_classify(
                                _text or "",
                                available_tools=_avail_tools,
                                enabled=_cap_enabled,
                                llm_registry=_gate_llm,
                            )
                            from agent.capability_gate import Verdict as _Verdict
                            if _verdict.verdict is _Verdict.REFUSE:
                                _refuse_text = _verdict.render_text()
                                logger.info(
                                    "capability_gate_refused sid=%s text=%s",
                                    _sid, (_text or "")[:120],
                                )
                                # 直接当一轮 assistant 回复落库 + 推前端，
                                # 不进 agent loop。
                                if _sdb is not None:
                                    try:
                                        _ref_id = await _sdb.append_message(
                                            session_id=_sid,
                                            role="assistant",
                                            content=_refuse_text,
                                        )
                                        if _vw is not None and _ref_id is not None:
                                            await _vw.enqueue(_ref_id, _refuse_text)
                                    except Exception as _ex:  # noqa: BLE001
                                        logger.warning(
                                            "capability_gate_persist_failed error=%s",
                                            _ex,
                                        )
                                await _ws.send_json({
                                    "type": "chat_v2_final",
                                    "payload": {
                                        "text": _refuse_text,
                                        "iterations": 0,
                                        "session_id": _sid,
                                    },
                                })
                                return
                        except Exception as _cap_exc:  # noqa: BLE001
                            # 能力门永远不能阻断正常对话——异常即放行。
                            logger.debug(
                                "capability_gate_skipped error=%s", _cap_exc
                            )

                    # ContextAssembler — 把长期记忆 / 技能 / MCP 工具描述
                    # 装入 message stack（继承自原 chat 路径，丢失就降级
                    # 到只发用户原话，永远不让 chat 因 assembler 异常炸）。
                    _bundle = None
                    _assembler = service_context.get("context_assembler")
                    if _assembler is not None and getattr(_assembler, "enabled", True):
                        try:
                            import time as _ti
                            # P4-S20-LLM-Unified: 把当前 LLM 的 model + base_url
                            # 传给 ContextAssembler，PersonaComponent 用它告诉
                            # 用户底层模型 — 不再有"我看不到模型"的尴尬回复。
                            # P4-S22: pass code_mode state into assembler
                            # so PersonaComponent picks the engineering
                            # assistant template + project root.
                            _cmm_for_assembler = service_context.get("code_mode")
                            _code_cfg = {"enabled": False, "project_root": ""}
                            if _cmm_for_assembler and _cmm_for_assembler.is_enabled(_sid):
                                _state = _cmm_for_assembler.get(_sid)
                                if _state and _state.project_root:
                                    _code_cfg = {
                                        "enabled": True,
                                        "project_root": str(_state.project_root),
                                    }
                            _bundle = await _assembler.assemble(
                                user_message=_text,
                                memory_manager=service_context.get("memory_manager"),
                                tool_registry=service_context.get("tool_router"),
                                skill_registry=service_context.get("skill_loader"),
                                mcp_manager=service_context.get("mcp_manager"),
                                session_id=_sid,
                                config={
                                    "llm": {
                                        "model": getattr(local_llm, "model", "unknown"),
                                        "base_url": getattr(local_llm, "base_url", ""),
                                    },
                                    "code_mode": _code_cfg,
                                },
                            )
                            if _bundle is not None and _bundle.decisions is not None:
                                _bundle.decisions.timestamp = _ti.time()
                                _bundle.decisions.session_id = _sid
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "p4_assembler_failed", error=str(exc),
                                error_type=type(exc).__name__,
                            )
                            _bundle = None

                    if _bundle is not None:
                        # P4-S21 #16 fix: pass real conversation history.
                        # Without this the LLM gets only the current user
                        # turn, even though SessionDB has all prior messages
                        # — that produced the "we just talked about VPN,
                        # why does it ask 'what do you want to do?'" bug.
                        # `bundle.history` is populated by MemoryComponent
                        # from raw L2 rows.
                        _msgs = _bundle.build_messages(
                            user_message=_text,
                            history=_bundle.history,
                        )
                    else:
                        _msgs = [{"role": "user", "content": _text}]

                    # 2026-05-16 bugfix（实测：companion 让生成 Excel，做到
                    # 一半 max_iter=8 触发 auto_resume，LLM 收到字面
                    # ``<<auto_resume>>`` 当用户消息 → "用户在测试系统" →
                    # 重新自我介绍 + 反问"要我做什么"，丢掉原任务）。
                    # sentinel 永远不该作为 LLM 的 user turn 出现；把最后一
                    # 条 user 消息的字面 sentinel 换成明确的续跑指令。具体
                    # "缺什么" 由紧随其后的 [Supervisor] system hint 补充；
                    # bundle.history 已带原任务上下文。
                    if _is_sentinel:
                        _resume_directive = (
                            "（系统自动续跑：你上一轮还没把用户请求的任务做完"
                            "就达到了迭代上限。**不要重新自我介绍、不要反问用户"
                            "想做什么**——回顾上面的对话历史与工具结果，找出用户"
                            "最初请求的那个任务还差哪些步骤，直接继续把它做完。）"
                        )
                        for _i in range(len(_msgs) - 1, -1, -1):
                            if _msgs[_i].get("role") == "user":
                                _msgs[_i] = {
                                    **_msgs[_i],
                                    "content": _resume_directive,
                                }
                                break
                        else:
                            _msgs.append(
                                {"role": "user", "content": _resume_directive}
                            )

                    # P5-S4: pop any queued supervisor hints for this sid
                    # and inject them at the top of the system stack as a
                    # single ``[Supervisor]`` system message. This is the
                    # consume-on-use point — by the time the agent loop
                    # starts running, hints are already gone from the queue.
                    try:
                        _nq_for_inject = service_context.get("nudge_queue")
                        if _nq_for_inject is not None:
                            _hints = await _nq_for_inject.pop_all(_sid)
                            if _hints:
                                from agent.nudge_queue import format_hints_for_injection as _fmt_hints
                                _hint_text = _fmt_hints(_hints)
                                if _hint_text:
                                    _hint_msg = {
                                        "role": "system",
                                        "content": _hint_text,
                                        "_is_supervisor_hint": True,
                                    }
                                    _insert_at = 0
                                    while _insert_at < len(_msgs) and _msgs[_insert_at].get("role") == "system":
                                        _insert_at += 1
                                    _msgs.insert(_insert_at, _hint_msg)
                                    # Mark each hint dispatched in audit trail
                                    _sdb_for_audit = service_context.get("session_db")
                                    if _sdb_for_audit is not None:
                                        for _h in _hints:
                                            try:
                                                await _sdb_for_audit.append_supervisor_hint(
                                                    session_id=_sid,
                                                    alert_id=_h.alert_id or "",
                                                    hint_text=_h.text,
                                                    action="dispatched",
                                                    severity=_h.severity,
                                                )
                                            except Exception as _ex2:
                                                logger.debug(
                                                    "supervisor_dispatched_audit_failed",
                                                    error=str(_ex2),
                                                )
                                    logger.info(
                                        "supervisor_hints_injected sid=%s count=%d",
                                        _sid,
                                        len(_hints),
                                    )
                    except Exception as _hint_exc:  # noqa: BLE001
                        logger.debug("supervisor_hint_inject_failed error=%s", _hint_exc)

                    final_text = ""
                    # P4-S24: capture the LAST assistant turn's
                    # reasoning_content so we persist it alongside
                    # final_text. Multi-iteration runs (tool calls)
                    # may produce reasoning_content per iteration —
                    # only the terminal one matters for round-trip,
                    # since intermediate turns are already complete
                    # in working_messages by the time agent_loop ends.
                    final_reasoning = ""
                    try:
                        from agent.agent_loop import (
                            AgentLoop as _AgentLoop,
                            AssistantMessageEvent as _AsstEv,
                            AssistantDeltaEvent as _AsstDelta,
                            ToolCallEvent as _TCEv,
                            ToolResultEvent as _TREv,
                            FinalEvent as _FinEv,
                            ErrorEvent as _ErrEv,
                        )
                        from agent.tool_use_shim import OpenAICompatibleAgentLLM as _Shim
                        # P4-S20-LLM-Unified: 单一 endpoint。local_llm 来自
                        # 统一 [llm] 段（base_url + api_key + model）；不管你
                        # 把它指向 Ollama 还是任何 OpenAI 兼容云端都一样。
                        _provider = local_llm or cloud_llm
                        _shim = _Shim(provider=_provider)
                        # P4-S22: Code mode bumps max_iterations to 50 so
                        # long tool-use chains (read → grep → edit → bash
                        # → repeat) can finish a real task.
                        # 2026-05-16: companion 8 → 16。实测"找桌面的
                        # 花名册并生成 Excel"这类正当多步任务（搜目录→
                        # 定位文件→读→生成→写）8 轮就爆，过早触发
                        # auto_resume。16 给真实任务留余量，仍远小于
                        # code 的 50，纯闲聊跑满 16 仍会被 supervisor 接住。
                        _cmm = service_context.get("code_mode")
                        _in_code_mode = bool(_cmm and _cmm.is_enabled(_sid))
                        _max_iter = 50 if _in_code_mode else 16

                        # P5-S2 D2: long tool_call args reliability hint.
                        # Models (deepseek-v4-pro etc.) corrupt JSON escapes
                        # in args > ~3000 chars about 30-40% of the time when
                        # streaming — observed parse_ok=False on 7552/7594/7661
                        # char write_file args. Guidance prepended to message
                        # stack so the model prefers small multi-call writes
                        # over a single huge one.
                        if _in_code_mode:
                            _long_args_hint = {
                                "role": "system",
                                "content": (
                                    "[Tool reliability guidance]\n"
                                    "When calling `write_file` (or any tool with a long string arg):\n"
                                    "1. Keep each call's `content` ≤ 2000 chars. Long strings (>3KB) "
                                    "are unreliable due to JSON escape errors in streaming output — "
                                    "the call will be rejected as malformed.\n"
                                    "2. To write a longer file: call write_file with `mode=\"write\"` "
                                    "for the first chunk, then call again with `mode=\"append\"` for "
                                    "each subsequent chunk. Two-three medium calls beat one giant call.\n"
                                    "3. If unsure of length, break early — over-splitting is free, "
                                    "but a corrupted single call wastes the whole turn."
                                ),
                            }
                            _insert_at = 0
                            while _insert_at < len(_msgs) and _msgs[_insert_at].get("role") == "system":
                                _insert_at += 1
                            _msgs.insert(_insert_at, _long_args_hint)

                        # P6 Phase 6 — ContextManager is always constructed
                        # here. The pre-call history compaction below runs
                        # AFTER ``_provider_chain`` resolves (it needs the
                        # chain's first provider as the summarize source).
                        # The legacy ``if _ctx_mgr is None:`` inline B2
                        # block (~60 lines re-implementing what
                        # chat_prep.prepare_chat_messages_for_chain does
                        # cleanly) was removed in Phase 6.
                        # Phase 1.1.4 — per-model context map wiring. Resolve
                        # this session's ModelContextInfo via the 3-layer
                        # chain (builtin ← %APPDATA% global ← project
                        # .deskpet/context.toml). Code mode passes its
                        # project_root so the project layer applies; non-code
                        # mode passes None (only builtin + global). The
                        # [context.manager].v2_enabled knob is the
                        # Strangler-Fig rollback闸 — false 退回 2026-05-15
                        # stop-gap 绝对值 ContextConfig，忽略 per-model map。
                        from agent.context_manager import ContextManager as _CtxMgr
                        _ctx_v2_enabled = bool(
                            ((config.raw.get("context") or {}).get("manager") or {})
                            .get("v2_enabled", True)
                        )
                        _ctx_model = getattr(_provider, "model", "") or "_default"
                        _ctx_proot = (
                            _cmm.project_root(_sid)
                            if (_in_code_mode and _cmm is not None)
                            else None
                        )
                        _ctx_mgr = _CtxMgr.for_session(
                            model=_ctx_model,
                            project_root=_ctx_proot,
                            v2_enabled=_ctx_v2_enabled,
                        )

                        # ─── P5-S2 Phase 3.15: provider_chain resolution ───
                        # If the LLMProviderRegistry is wired up (Phase 1+2)
                        # and we have a SessionDB, resolve a chain for THIS
                        # session (code → per-session binding may pin to one
                        # provider; companion → always global chain). Each
                        # ProviderEntry becomes a fresh OpenAICompatibleProvider
                        # instance with its own api_key from keychain. Empty
                        # chain leaves _provider_chain=None so AgentLoop falls
                        # through to its legacy single-provider path.
                        _provider_chain: list[Any] | None = None
                        try:
                            _registry = service_context.get("provider_registry")
                            if _registry is not None and _sdb is not None:
                                from llm.resolution import resolve_provider_for_session as _resolve_chain
                                # code-session-model-params: code-mode
                                # default model (Strangler-Fig — empty/
                                # absent ⇒ None ⇒ legacy shared model;
                                # pet/companion never pass this, untouched).
                                _agent_cfg = (config.raw.get("agent") if hasattr(config, "raw") else None) or {}
                                _code_default_model = (
                                    (str(_agent_cfg.get("code_model") or "").strip() or None)
                                    if _in_code_mode
                                    else None
                                )
                                _entries = await _resolve_chain(
                                    _sid,
                                    is_code_session=_in_code_mode,
                                    registry=_registry,
                                    session_db=_sdb,
                                    code_default_model=_code_default_model,
                                )
                                if _entries:
                                    _chain: list[Any] = []
                                    for _entry in _entries:
                                        _api_key = _registry.resolve_api_key(_entry.id) or "ollama"
                                        _chain.append(OpenAICompatibleProvider(
                                            base_url=_entry.base_url,
                                            api_key=_api_key,
                                            model=_entry.model,
                                            temperature=getattr(_entry, "temperature", 0.7),
                                            sanitize_inline_cot_dsml=_sanitize_cot_dsml,
                                            code_params=getattr(_entry, "code_params", None),
                                        ))
                                    _provider_chain = _chain
                        except Exception as _resolve_exc:  # noqa: BLE001
                            logger.warning(
                                "p5s2_provider_chain_resolve_failed sid=%s err=%s — falling back to legacy single-provider path",
                                _sid, str(_resolve_exc)[:200],
                            )
                            _provider_chain = None

                        # P6 Phase 6 — ContextManager-based preflight
                        # compaction. Uses ``_provider_chain[0]`` (resolved
                        # above) for the summarize step. Best-effort: any
                        # exception falls back to the original messages
                        # (better long context than a hard error mid-task).
                        try:
                            from agent.chat_prep import (
                                prepare_chat_messages_for_chain as _prep_chat_msgs,
                            )
                            _orig_len = len(_msgs)
                            _msgs = await _prep_chat_msgs(
                                _msgs,
                                provider_chain=_provider_chain,
                                ctx_mgr=_ctx_mgr,
                                fallback_summarizer=local_llm,
                            )
                            if len(_msgs) != _orig_len:
                                logger.info(
                                    "p6_history_compacted sid=%s orig=%d new=%d",
                                    _sid, _orig_len, len(_msgs),
                                )
                        except Exception as _p6_exc:  # noqa: BLE001
                            logger.warning(
                                "p6_chat_prep_failed sid=%s err=%s",
                                _sid, str(_p6_exc)[:200],
                            )

                        # Inject project root into per-session tool-arg
                        # context so glob/grep can run without the LLM
                        # restating the path every call. Cleared when
                        # code mode exits via ``code_mode_exit`` IPC.
                        if _in_code_mode:
                            _proot = _cmm.project_root(_sid)
                            if _proot is not None:
                                deskpet_tool_registry_v2.set_session_context(
                                    _sid,
                                    {"_project_root": str(_proot)},
                                )
                        else:
                            # OpenSpec 2026-05-16 §D3 — companion session
                            # write-scope。陪伴 session 的写盘类工具 path
                            # 限定在 resolve(workspace_root) 内（默认
                            # <user_data_dir>/workspace）；越界 → 工具
                            # 返回引导文案让用户进 code 模式。这不是
                            # 沙箱（不拦读/命令/弹窗），是 session 类型
                            # 语义。``set_session_context`` 注入的
                            # ``_write_scope_root`` 会被 execute_tool 合并
                            # 进每次 tool 调用 params，写盘工具据此校验。
                            # write_scope_enforced=false → 不注入 + 清掉
                            # 残留 → 工具读不到该键 → 退回旧自由写盘。
                            try:
                                _comp_cfg = (config.raw.get("companion") or {})
                                _ws_enforced = bool(
                                    _comp_cfg.get("write_scope_enforced", True)
                                )
                                # OpenSpec 2026-05-16-async-image-gen:
                                # generate_image needs _session_id (route
                                # the completion back to this pet session)
                                # + _image_worker (submit target). Inject
                                # them ALWAYS via the same merge mechanism;
                                # _write_scope_root is added conditionally.
                                _sctx: dict[str, Any] = {
                                    "_session_id": _sid,
                                    "_image_worker": service_context.get(
                                        "image_worker"
                                    ),
                                }
                                if _ws_enforced:
                                    from agent.write_scope import (
                                        resolve_workspace_root as _resolve_ws,
                                    )
                                    _ws_root = _resolve_ws(
                                        configured=str(
                                            _comp_cfg.get("workspace_root", "")
                                        )
                                    )
                                    _sctx["_write_scope_root"] = str(_ws_root)
                                deskpet_tool_registry_v2.set_session_context(
                                    _sid, _sctx
                                )
                            except Exception as _ws_exc:  # noqa: BLE001
                                logger.debug(
                                    "companion_write_scope_skipped sid=%s err=%s",
                                    _sid, _ws_exc,
                                )

                        # P4-S25 A2: Plan/Replan — for non-trivial code-mode
                        # requests, do a structured-output plan call BEFORE
                        # the ReAct loop. The plan is sent to the frontend
                        # for visibility and injected into the message stack
                        # so the LLM stays anchored. Auto-confirm (no user
                        # gate) for now — the 停止 button is the escape
                        # hatch if the plan looks wrong.
                        try:
                            from agent.plan import (
                                maybe_extract_plan as _maybe_plan,
                                plan_to_system_message as _plan_to_sys,
                            )
                            _plan = await _maybe_plan(
                                _provider,
                                _text,
                                str(_cmm.project_root(_sid)) if _in_code_mode and _cmm else None,
                                in_code_mode=_in_code_mode,
                            )
                            if _plan is not None:
                                await _ws.send_json({
                                    "type": "chat_v2_plan",
                                    "payload": {
                                        "session_id": _sid,
                                        "rationale": _plan.rationale,
                                        "steps": [
                                            {"title": s.title, "detail": s.detail}
                                            for s in _plan.steps
                                        ],
                                    },
                                })
                                # Insert plan as a system message right after
                                # the existing system stack (or at index 0
                                # if there's nothing).
                                _plan_msg = {"role": "system", "content": _plan_to_sys(_plan)}
                                _insert_at = 0
                                while _insert_at < len(_msgs) and _msgs[_insert_at].get("role") == "system":
                                    _insert_at += 1
                                _msgs.insert(_insert_at, _plan_msg)
                        except Exception as _exc:  # noqa: BLE001
                            logger.debug("p4s25_plan_skipped error=%s", _exc)

                        # P5-S2 Hook A: completion guard probe.
                        #
                        # Maps base_session_id → code_session_id (via
                        # CodeModeManager) → reads incomplete code_todos
                        # from SessionDB. Returned to AgentLoop so it
                        # can rebound the LLM with a "you said done but
                        # X todos still pending" system message instead
                        # of finalizing prematurely.
                        #
                        # Companion-mode sessions (no code_session_id)
                        # short-circuit to []: no todos in scope, no
                        # rebound. Only Code-mode sessions get the
                        # completion check.
                        async def _completion_probe(_base_sid: str) -> list[dict]:
                            try:
                                cm_local = service_context.get("code_mode")
                                if cm_local is None:
                                    return []
                                code_sid = cm_local.code_session_id(_base_sid)
                                if not code_sid:
                                    return []
                                if _sdb is None:
                                    return []
                                rows = await _sdb.get_code_todos(code_sid)
                                return [
                                    r for r in rows
                                    if (r.get("status") or "").lower() not in ("completed", "cancelled")
                                ]
                            except Exception as _e:  # noqa: BLE001
                                logger.warning(
                                    "p5s2_completion_probe_lookup_failed sid=%s err=%s",
                                    _base_sid, str(_e)[:200],
                                )
                                return []

                        # P5-S2 Phase 6: pull supervisor-section knobs
                        # for the AgentLoop's in-loop death-loop
                        # suppression. Read fresh from config so a
                        # config reload (future) takes effect on the
                        # next chat task without restart.
                        _sig_repeat_thr = int(
                            ((config.raw.get("supervisor") or {}).get(
                                "tool_signature_repeat_threshold", 3
                            ))
                        )
                        _agent = _AgentLoop(
                            llm_registry=_shim,
                            tool_registry=deskpet_tool_registry_v2,
                            max_iterations=_max_iter,
                            completion_probe=_completion_probe,
                            max_completion_nudges=2,
                            signature_repeat_threshold=_sig_repeat_thr,
                            context_manager=_ctx_mgr,
                        )
                        # P4-S25 A1: stream by default — gives the user
                        # instant visible feedback on thinking-mode
                        # models that otherwise stare back blank for 30+
                        # seconds. Caller-side `chat_v2_delta` event is
                        # accumulated by the frontend MessageStream.

                        # P5-S1: SessionActivity bumping. Every AgentEvent
                        # for a Code-mode session feeds the supervisor
                        # watchdog's view of the world (last_event_ts,
                        # recent_events ring buffer, tool signature window).
                        # Companion-mode sessions are not tracked.
                        _sa_store = service_context.get("session_activity") if _in_code_mode else None
                        if _sa_store is not None:
                            try:
                                await _sa_store.set_status(_sid, "running")
                            except Exception:
                                pass

                        async for ev in _agent.run(
                            _msgs,
                            session_id=_sid,
                            stream=True,
                            provider_chain=_provider_chain,
                        ):
                            # P5-S1: bump activity BEFORE forwarding so the
                            # watchdog sees the latest event even if the
                            # WS send fails. Best-effort — never let a
                            # bump failure abort the agent loop.
                            if _sa_store is not None:
                                try:
                                    if isinstance(ev, _TCEv) and ev.tool_call:
                                        await _sa_store.bump(
                                            _sid,
                                            event_type="tool_call",
                                            name=ev.tool_call.name,
                                            args=ev.tool_call.arguments,
                                            iteration=ev.iteration,
                                            max_iterations=_max_iter,
                                        )
                                    elif isinstance(ev, _TREv):
                                        await _sa_store.bump(
                                            _sid,
                                            event_type="tool_result",
                                            name=ev.tool_name,
                                            ok=True,
                                            snippet=(ev.result or "")[:80],
                                            iteration=ev.iteration,
                                            max_iterations=_max_iter,
                                        )
                                    elif isinstance(ev, _AsstEv):
                                        await _sa_store.bump(
                                            _sid,
                                            event_type="assistant_message",
                                            iteration=ev.iteration,
                                            max_iterations=_max_iter,
                                        )
                                    elif isinstance(ev, _FinEv):
                                        await _sa_store.bump(
                                            _sid,
                                            event_type="final",
                                            iteration=ev.iteration,
                                            max_iterations=_max_iter,
                                        )
                                        await _sa_store.set_status(_sid, "idle")
                                    elif isinstance(ev, _ErrEv):
                                        await _sa_store.bump(
                                            _sid,
                                            event_type="error",
                                            snippet=(ev.detail or ev.reason or "")[:80],
                                            iteration=ev.iteration,
                                            max_iterations=_max_iter,
                                        )
                                        await _sa_store.mark_error_pending(_sid)
                                except Exception as _bump_exc:  # noqa: BLE001
                                    logger.debug("session_activity_bump_failed", error=str(_bump_exc))
                            if isinstance(ev, _AsstDelta):
                                await _ws.send_json({
                                    "type": "chat_v2_delta",
                                    "payload": {
                                        "session_id": _sid,
                                        "kind": ev.kind,
                                        "content": ev.content,
                                        "iteration": ev.iteration,
                                    },
                                })
                            elif isinstance(ev, _AsstEv):
                                # P4-S20-LLM-Unified-fix: AgentLoop 在每次
                                # LLM turn 后 emit AsstEv，最终轮还会 emit
                                # FinalEvent —— 两者带相同 content。前端
                                # 两条都渲染会造成重复。规则：只在中间
                                # 步骤（带 tool_calls）emit chat_response
                                # 作为"思考中"提示；最终回复让 FinalEvent
                                # 唯一负责。
                                # P4-S23: stamp session_id on every event
                                # so the frontend can route it to the right
                                # tile / chat slot when multiple sessions
                                # are running.
                                if ev.content and ev.tool_calls:
                                    await _ws.send_json({
                                        "type": "chat_response",
                                        "payload": {"text": ev.content, "provider": "v2", "session_id": _sid},
                                    })
                            elif isinstance(ev, _TCEv) and ev.tool_call:
                                # Emit BOTH the legacy tool_use_event (kept
                                # for the pet's permission popup wiring)
                                # AND a code-panel-friendly `tool_call`
                                # event so the new MessageStream renders
                                # ToolCallCard inline.
                                await _ws.send_json({
                                    "type": "tool_use_event",
                                    "payload": {
                                        "kind": "request",
                                        "tool_name": ev.tool_call.name,
                                        "params": ev.tool_call.arguments,
                                        "turn": ev.iteration,
                                        "session_id": _sid,
                                    },
                                })
                                await _ws.send_json({
                                    "type": "tool_call",
                                    "payload": {
                                        "name": ev.tool_call.name,
                                        "arguments": ev.tool_call.arguments,
                                        "turn": ev.iteration,
                                        "session_id": _sid,
                                    },
                                })
                                # P6 bugfix 2026-05-14 (history persistence):
                                # tool_call 也要入 SessionDB，否则重启或 F5 后
                                # UI 只能看到 user 气泡，看不到 agent 调用过
                                # 什么工具。schema 早就支持 (role='assistant'
                                # + tool_calls JSON 列)，main.py 之前没用。
                                if _sdb is not None:
                                    try:
                                        import json as _persist_json
                                        await _sdb.append_message(
                                            session_id=_sid,
                                            role="assistant",
                                            content="",
                                            tool_calls=[{
                                                "id": ev.tool_call.id,
                                                "type": "function",
                                                "function": {
                                                    "name": ev.tool_call.name,
                                                    "arguments": _persist_json.dumps(
                                                        ev.tool_call.arguments,
                                                        ensure_ascii=False,
                                                    ),
                                                },
                                            }],
                                        )
                                    except Exception as exc:  # noqa: BLE001
                                        logger.warning(
                                            "chat_persist_tool_call_failed",
                                            error=str(exc),
                                        )
                            elif isinstance(ev, _TREv):
                                try:
                                    _parsed = json.loads(ev.result)
                                except Exception:
                                    _parsed = ev.result
                                await _ws.send_json({
                                    "type": "tool_use_event",
                                    "payload": {
                                        "kind": "result",
                                        "tool_name": ev.tool_name,
                                        "result": _parsed,
                                        "turn": ev.iteration,
                                        "session_id": _sid,
                                    },
                                })
                                await _ws.send_json({
                                    "type": "tool_result",
                                    "payload": {
                                        "tool": ev.tool_name,
                                        "ok": True,  # _TREv only fires on success; failures arrive as _ErrEv
                                        "result": ev.result,
                                        "turn": ev.iteration,
                                        "session_id": _sid,
                                    },
                                })
                                # P6 bugfix 2026-05-14 (history persistence):
                                # tool_result 也要入 SessionDB (role='tool'
                                # + tool_call_id 回指 assistant 的调用)。
                                if _sdb is not None:
                                    try:
                                        import json as _persist_json
                                        _result_content = (
                                            ev.result if isinstance(ev.result, str)
                                            else _persist_json.dumps(ev.result, ensure_ascii=False)
                                        )
                                        await _sdb.append_message(
                                            session_id=_sid,
                                            role="tool",
                                            content=_result_content,
                                            tool_call_id=getattr(ev, "tool_call_id", "") or "",
                                        )
                                    except Exception as exc:  # noqa: BLE001
                                        logger.warning(
                                            "chat_persist_tool_result_failed",
                                            error=str(exc),
                                        )
                            elif isinstance(ev, _FinEv):
                                final_text = ev.content
                                final_reasoning = ev.reasoning_content
                                # P4-S24: persist the assistant row IN-LINE
                                # so a same-sid cancellation (next user
                                # message arriving before this task's
                                # post-loop tail finishes) can't strand
                                # it. Without this, the next turn's
                                # history rebuild misses the prior
                                # assistant entirely. asyncio.shield
                                # would do too but inlining is simpler
                                # and the persist cost is sub-ms anyway.
                                # P6 bugfix 2026-05-14: 即使 final_text 空也
                                # 持久化（保留 turn 边界）。空文本仍写一条
                                # role='assistant' 行表示"agent 在此 end_turn
                                # 了"，让 history 上下文连贯——之前 if
                                # final_text 的 guard 导致 tool_use loop 后
                                # 的 end_turn 完全没记录。
                                if _sdb is not None:
                                    try:
                                        _asst_id_inline = await _sdb.append_message(
                                            session_id=_sid,
                                            role="assistant",
                                            content=final_text or "",
                                            reasoning_content=(final_reasoning or None),
                                        )
                                        if (
                                            _vw is not None
                                            and _asst_id_inline is not None
                                            and final_text
                                        ):
                                            await _vw.enqueue(_asst_id_inline, final_text)
                                    except Exception as exc:  # noqa: BLE001
                                        logger.warning(
                                            "chat_persist_assistant_failed",
                                            error=str(exc),
                                        )
                                await _ws.send_json({
                                    "type": "chat_v2_final",
                                    "payload": {"text": ev.content, "iterations": ev.iteration, "session_id": _sid},
                                })
                                # P5-S2 Phase 4: if this final came from
                                # an auto-resume cycle (attempts > 0),
                                # emit ``auto_resume_succeeded`` so the
                                # frontend banner can switch to a
                                # success state. We don't reset the
                                # counter here — only a fresh USER
                                # message resets (per spec: the user's
                                # implicit grant of a new budget).
                                try:
                                    _sa_for_check = service_context.get("session_activity")
                                    if _sa_for_check is not None:
                                        _sa_obj = await _sa_for_check.get(_sid)
                                        if _sa_obj is not None and _sa_obj.auto_resume_attempts > 0:
                                            await _ws.send_json({
                                                "type": "auto_resume_succeeded",
                                                "payload": {
                                                    "session_id": _sid,
                                                    "attempts": _sa_obj.auto_resume_attempts,
                                                },
                                            })
                                            logger.info(
                                                "auto_resume_succeeded sid=%s attempts=%d",
                                                _sid, _sa_obj.auto_resume_attempts,
                                            )
                                except Exception as _ex:  # noqa: BLE001
                                    logger.debug("auto_resume_success_emit_failed sid=%s err=%s", _sid, _ex)
                            elif isinstance(ev, _ErrEv):
                                # P5-S2 Phase 4: try AutoResumeOrchestrator
                                # FIRST for recoverable error reasons. If
                                # the orchestrator decides to spawn a fresh
                                # task, we suppress the chat_v2_error to
                                # the user (the auto_resume_started ws
                                # event already shows them a banner). If
                                # it decides ask_user / exhausted, fall
                                # through to the legacy error emit so the
                                # user sees the popup as before.
                                _ar_handled = False
                                try:
                                    from agent.auto_resume import is_auto_resume_trigger as _is_ar
                                    if _is_ar(ev.reason or ""):
                                        _orch_inst = service_context.get("auto_resume")
                                        if _orch_inst is not None:
                                            _snap_for_orch = {
                                                "session_id": _sid,
                                                "reason": ev.reason,
                                                "detail": ev.detail,
                                                "iteration": ev.iteration,
                                            }
                                            _ar_result = await _orch_inst.handle_failure(
                                                _sid, ev.reason or "", _snap_for_orch, _msgs,
                                            )
                                            if _ar_result.action == "spawned":
                                                # Orchestrator owns the user-facing event now.
                                                _ar_handled = True
                                                logger.info(
                                                    "auto_resume_engaged sid=%s reason=%s attempt=%d",
                                                    _sid, ev.reason, _ar_result.attempt,
                                                )
                                            elif _ar_result.action == "exhausted":
                                                # auto_resume_exhausted ws event already
                                                # emitted by orchestrator; suppress legacy
                                                # error so frontend doesn't double-popup.
                                                _ar_handled = True
                                except Exception as _ex:  # noqa: BLE001
                                    logger.debug("auto_resume_handle_failed sid=%s err=%s", _sid, _ex)

                                if not _ar_handled:
                                    # P4-S25 (2026-05-09): cross-endpoint
                                    # fallback removed. LLM errors now
                                    # surface to the user directly.
                                    await _ws.send_json({
                                        "type": "chat_v2_error",
                                        "payload": {"reason": ev.reason, "detail": ev.detail, "session_id": _sid},
                                    })

                        # P4-S24: assistant persistence moved INTO the
                        # FinalEvent handler above so a same-sid task
                        # cancellation (race when user fires the next
                        # message faster than the post-loop tail can
                        # finish) doesn't strand the assistant row.
                        # The `final_text and _sdb` guard there already
                        # mirrors what this block used to do.

                        # ContextAssembler feedback — 写入这一轮最终
                        # 响应到决策表，让 ContextTracePanel 能看到时长。
                        if _bundle is not None and _assembler is not None:
                            try:
                                _assembler.feedback(_bundle, final_response=final_text)
                            except Exception as exc:  # noqa: BLE001
                                logger.warning("p4_assembler_feedback_failed", error=str(exc))

                        # BillingLedger — 取 provider 的 last_usage 计费。
                        # 单 endpoint 时统一调 _provider；URL 是 localhost
                        # 就不会被计费（ledger.record_if_billable 自动判断）。
                        usage = getattr(_provider, "last_usage", None)
                        if usage:
                            try:
                                _is_local = "localhost" in (
                                    getattr(_provider, "base_url", "") or ""
                                ) or "127.0.0.1" in (
                                    getattr(_provider, "base_url", "") or ""
                                )
                                await billing_ledger.record(
                                    provider="local" if _is_local else "cloud",
                                    model=getattr(_provider, "model", "unknown"),
                                    prompt_tokens=int(usage.get("prompt_tokens", 0)),
                                    completion_tokens=int(usage.get("completion_tokens", 0)),
                                )
                            except Exception as exc:  # noqa: BLE001
                                logger.warning("billing_record_failed", error=str(exc))
                            _provider.last_usage = None

                    except Exception as exc:  # noqa: BLE001
                        # P6 bugfix 2026-05-14 (live-test): ASGI ws-close
                        # races during long streams produce the noisy
                        # "Unexpected ASGI message 'websocket.send', after
                        # sending 'websocket.close'" RuntimeError. This is
                        # benign — the client just disconnected mid-stream
                        # (panel close, network blip, ws client timeout).
                        # Demote to debug + skip the chat_v2_error send (it
                        # would just trigger the same error again).
                        _msg = str(exc)
                        _is_ws_closed = (
                            isinstance(exc, RuntimeError)
                            and "websocket" in _msg.lower()
                            and ("close" in _msg.lower() or "completed" in _msg.lower())
                        )
                        if _is_ws_closed:
                            logger.debug(
                                "chat_v2_ws_closed_midstream sid=%s — client disconnected",
                                _sid,
                            )
                            return  # don't try to send chat_v2_error on closed ws
                        logger.warning(
                            "chat_v2_failed",
                            error=str(exc),
                            error_type=type(exc).__name__,
                        )
                        # P4-S23: include session_id so the panel can
                        # show the error on the right tile/slot.
                        # P4-S22 carryover: also include error_type so
                        # the UI can display "ConnectError" instead of
                        # falling back to "unknown" when str(exc) is "".
                        try:
                            await _ws.send_json({
                                "type": "chat_v2_error",
                                "payload": {
                                    "error": str(exc) or type(exc).__name__,
                                    "detail": type(exc).__name__,
                                    "session_id": _sid,
                                },
                            })
                        except Exception:
                            pass

                # Fire-and-forget — recv loop must keep draining
                # permission_response while we're awaiting the gate.
                # P4-S23: stamp the per-message session_id so multi-
                # session panels work correctly. Track in-flight task
                # per-sid so a same-sid retry cancels its predecessor
                # (prevents stale tool calls if user rage-types).
                _prev_task = _chat_inflight.get(_msg_sid)
                if _prev_task is not None and not _prev_task.done():
                    _prev_task.cancel()
                _chat_task = asyncio.create_task(_run_chat(ws, text, _msg_sid))
                _chat_inflight[_msg_sid] = _chat_task

                # P5-S2 Phase 4: register a per-sid re-dispatcher closure
                # so the AutoResumeOrchestrator can spawn a follow-up
                # task without needing access to the chat-handler-scoped
                # ``_run_chat``. The closure captures the latest _run_chat
                # and is overwritten on every user turn, so it always
                # reflects the freshest local state.
                async def _redisp(_target_ws, _target_sid):
                    _new_task = asyncio.create_task(
                        _run_chat(_target_ws, "<<auto_resume>>", _target_sid)
                    )
                    _chat_inflight[_target_sid] = _new_task
                    _new_task.add_done_callback(_make_followup_cb(_target_sid, _target_ws))

                _auto_resume_redispatchers[_msg_sid] = _redisp

                # P5-S4: register a done_callback so when this task
                # finishes (success or cancel), we check whether the
                # supervisor pushed a hint while it was running and
                # automatically schedule a follow-up turn to consume it.
                # Critical safety property: if the user retried in the
                # meantime, _chat_inflight[sid] points at a NEWER task —
                # we detect that and skip, letting the new task consume
                # the hint via its own message-build pop_all path.
                def _make_followup_cb(target_sid: str, target_ws):
                    own_task = _chat_task

                    def _cb(_t):
                        async def _maybe_followup():
                            try:
                                cur = _chat_inflight.get(target_sid)
                                if cur is not own_task:
                                    # User retried (or another follow-up
                                    # took over) — let that task handle hints.
                                    return
                                _nq_check = service_context.get("nudge_queue")
                                if _nq_check is None or not await _nq_check.peek(target_sid):
                                    return
                                # Schedule a follow-up turn with synthesized
                                # trigger text. ``_run_chat`` will pop the
                                # hint via the standard injection path.
                                new_task = asyncio.create_task(
                                    _run_chat(target_ws, "<<supervisor_followup>>", target_sid)
                                )
                                _chat_inflight[target_sid] = new_task
                                new_task.add_done_callback(_make_followup_cb(target_sid, target_ws))
                                logger.info(
                                    "supervisor_followup_scheduled sid=%s",
                                    target_sid,
                                )
                            except Exception as _exc:  # noqa: BLE001
                                logger.debug(
                                    "supervisor_followup_failed sid=%s error=%s",
                                    target_sid,
                                    _exc,
                                )

                        try:
                            asyncio.create_task(_maybe_followup())
                        except RuntimeError:
                            # No running loop (shutdown); silently skip.
                            pass

                    return _cb

                _chat_task.add_done_callback(_make_followup_cb(_msg_sid, ws))

            elif msg_type == "interrupt":
                # Forward barge-in to the audio pipeline (separate WS). Cancels
                # in-flight ASR/LLM/TTS so user's new utterance gets priority.
                pipeline = _pipelines.get(session_id)
                if pipeline is not None:
                    pipeline.interrupt()
                    logger.info("interrupt dispatched", session_id=session_id)
                else:
                    logger.info("interrupt received but no active pipeline", session_id=session_id)
                await ws.send_json({"type": "interrupt_ack"})

            elif msg_type == "budget_status":
                # P2-1-S8: SettingsPanel "今日使用" pulls from here.
                try:
                    status = await billing_ledger.status()
                    await ws.send_json({"type": "budget_status", "payload": status})
                except Exception as exc:
                    logger.warning("budget_status_failed", error=str(exc))
                    await ws.send_json({
                        "type": "error",
                        "payload": {"message": f"budget_status failed: {exc}"},
                    })

            elif msg_type in ("memory_list", "memory_delete", "memory_clear", "memory_export"):
                # S14 (V5 §6 threat 5): user-facing controls over persisted
                # conversation history. All four go through the same memory
                # store the agent reads from, so redaction-on-write still holds.
                await _handle_memory_message(ws, session_id, msg_type, raw.get("payload", {}) or {})

            elif msg_type in p4_ipc.P4_IPC_MESSAGE_TYPES:
                # P4-S11 (§16.8): MemoryPanel + ContextTrace IPC surface.
                # Gracefully degrades when P4 services aren't registered
                # (pre-S12 wire-in) — UI shows empty state instead of error.
                await p4_ipc.handle(
                    ws,
                    session_id,
                    msg_type,
                    raw.get("payload", {}) or {},
                    service_context,
                )

            elif msg_type == "provider_test_connection":
                # P2-1-S3: SettingsPanel「测试连接」button. The candidate
                # credentials travel through the already-authenticated control
                # channel; nothing is persisted here — the UI saves via the
                # Tauri `set_cloud_api_key` command only on success.
                from provider_test_connection import handle_provider_test_connection
                await handle_provider_test_connection(ws, raw.get("payload", {}) or {})

            elif msg_type == "supervisor_user_choice":
                # P5-S2/S3: user clicked a button on the supervisor bubble.
                # P5-S1 D fix: also act on the choice — "Continue/重试/let it"
                # triggers a follow-up that consumes the queued hint;
                # "Cancel/中断/stop" cancels in-flight + clears queue.
                _payload = raw.get("payload", {}) or {}
                _target_sid = _payload.get("session_id") or session_id
                _alert_id = str(_payload.get("alert_id") or "")
                _btn_idx = int(_payload.get("button_index") or 0)
                _btn_text = str(_payload.get("button_text") or "")
                logger.info(
                    "supervisor_user_choice sid=%s alert=%s button_idx=%d text=%s",
                    _target_sid, _alert_id, _btn_idx, _btn_text,
                )
                _sdb_for_choice = service_context.get("session_db")
                if _sdb_for_choice is not None:
                    try:
                        await _sdb_for_choice.append_supervisor_hint(
                            session_id=_target_sid,
                            alert_id=_alert_id,
                            hint_text=_btn_text,
                            action="user_choice",
                            severity="green",
                            user_button=_btn_text,
                        )
                    except Exception as _exc:
                        logger.debug("supervisor_user_choice_audit_failed error=%s", _exc)

                # Heuristic intent matching by button text. We accept
                # both Chinese and English labels since the LLM picks
                # them dynamically. Stop/cancel takes priority over
                # continue if both keywords appear.
                _btn_lower = _btn_text.lower().strip()
                _is_stop = any(k in _btn_text for k in ("中断", "停止", "取消", "终止")) or \
                           any(k in _btn_lower for k in ("cancel", "stop", "abort", "interrupt"))
                _is_continue = any(k in _btn_text for k in ("继续", "重试", "再试", "让它", "再等")) or \
                               any(k in _btn_lower for k in ("continue", "retry", "let", "wait"))

                if _is_stop:
                    # Cancel any in-flight task + clear the nudge queue
                    _prev = _chat_inflight.get(_target_sid)
                    if _prev is not None and not _prev.done():
                        _prev.cancel()
                        logger.info("supervisor_user_choice_cancelled sid=%s", _target_sid)
                    _nq_for_stop = service_context.get("nudge_queue")
                    if _nq_for_stop is not None:
                        try:
                            await _nq_for_stop.clear(_target_sid)
                        except Exception:
                            pass
                elif _is_continue:
                    # Trigger a follow-up that consumes the queued hint.
                    # If a chat task is already running, leave it alone —
                    # the existing done_callback will handle the queue.
                    _cur = _chat_inflight.get(_target_sid)
                    if _cur is None or _cur.done():
                        # P6 bugfix 2026-05-14 (live-test): _run_chat 是
                        # chat_v2 分支的 nested async def (line ~2707)，与
                        # 本分支并列。Python 函数作用域规则：outer 函数里
                        # 任何分支定义过的局部变量都视为整个函数的 local，
                        # 但**未走该分支时未赋值** → UnboundLocalError。
                        # 之前用户点"允许并继续"按钮时如果该 ws 连接还没
                        # 跑过 chat_v2，就会崩这里。fallback：locals() 检
                        # 测 → 缺失就给用户友好提示 + 用 _chat_inflight 已
                        # 注册的 redispatcher（如果存在）兜底。
                        _run_chat_fn = locals().get("_run_chat")
                        if _run_chat_fn is None:
                            logger.warning(
                                "supervisor_user_choice_followup_skipped sid=%s "
                                "reason=dispatcher_not_initialized",
                                _target_sid,
                            )
                            try:
                                await ws.send_json({
                                    "type": "chat_v2_error",
                                    "payload": {
                                        "session_id": _target_sid,
                                        "reason": "dispatcher_not_ready",
                                        "detail": (
                                            "supervisor 后续路径还没准备好。"
                                            "请在输入框直接重发一条消息即可继续任务。"
                                        ),
                                    },
                                })
                            except Exception:
                                pass
                        else:
                            try:
                                _new_task = asyncio.create_task(
                                    _run_chat_fn(ws, "<<supervisor_followup>>", _target_sid)
                                )
                                _chat_inflight[_target_sid] = _new_task
                                logger.info(
                                    "supervisor_user_choice_followup_scheduled sid=%s",
                                    _target_sid,
                                )
                            except Exception as _ex_fu:
                                logger.warning(
                                    "supervisor_user_choice_followup_failed sid=%s error=%s",
                                    _target_sid, _ex_fu,
                                )

            elif msg_type == "supervisor_toggle":
                # P5-S2: enable / disable the supervisor at runtime.
                # When disabling: cancel watchdog, clear queues, drop activity.
                # When enabling: rebuild watchdog if not present (best-effort —
                # full restart is recommended for reliability).
                _payload = raw.get("payload", {}) or {}
                _enabled = bool(_payload.get("enabled", True))
                _wd_inst = service_context.get("watchdog")
                if _enabled:
                    if _wd_inst is None or not _wd_inst.is_running():
                        logger.info("supervisor_toggle_enable_requested (restart_recommended)")
                    else:
                        _wd_inst.enable()
                        logger.info("supervisor_toggle_enabled")
                else:
                    if _wd_inst is not None:
                        _wd_inst.disable()
                    _nq_inst = service_context.get("nudge_queue")
                    if _nq_inst is not None:
                        try:
                            await _nq_inst.clear()
                        except Exception:
                            pass
                    logger.info("supervisor_toggle_disabled")
                await ws.send_json({
                    "type": "supervisor_toggle_ack",
                    "payload": {"enabled": _enabled},
                })

            elif msg_type == "__debug_force_error_pending":
                # P5-S1 D — force a session into error_pending so the
                # next watchdog tick triggers supervisor. Used to test
                # fallback alert when supervisor LLM also fails.
                _payload = raw.get("payload", {}) or {}
                target_sid = _payload.get("session_id") or ""
                _sa = service_context.get("session_activity")
                if _sa is not None and target_sid:
                    await _sa.bump(target_sid, event_type="error", snippet="forced for E2E test")
                    await _sa.mark_error_pending(target_sid)
                    # Also reset watchdog dedup so next scan fires
                    _wd = service_context.get("watchdog")
                    if _wd is not None:
                        _wd.reset_dedup(target_sid)
                    await ws.send_json({
                        "type": "__debug_force_error_pending_ack",
                        "payload": {"sid": target_sid, "ok": True},
                    })

            elif msg_type == "__debug_dump_session_activity":
                # P5-S1 D — dump current session_activity store so we
                # can verify error_pending got set + watchdog ran.
                _sa = service_context.get("session_activity")
                _wd = service_context.get("watchdog")
                if _sa is not None:
                    _all = await _sa.snapshot_all()
                else:
                    _all = {}
                _wd_state = {
                    "running": _wd.is_running() if _wd else False,
                    "last_scans": dict(_wd._last_scan_ts) if _wd else {},
                }
                await ws.send_json({
                    "type": "__debug_dump_response",
                    "payload": {"session_activity": _all, "watchdog": _wd_state},
                })

            elif msg_type == "__debug_inject_supervisor_alert":
                # P5-S1 D — Debug IPC for visual / E2E testing. Already
                # protected by SHARED_SECRET (the ws connection itself
                # requires it), so we don't double-gate on DEV_MODE.
                # Single-user desktop-pet threat model: anyone with the
                # secret already has full backend access.
                _payload = raw.get("payload", {}) or {}
                # Fan out to every control WS, identical path to the
                # real supervisor's broadcast.
                msg = {"type": "supervisor_alert", "payload": _payload}
                for _sid_key, _ws_obj in list(_control_connections.items()):
                    try:
                        await _ws_obj.send_json(msg)
                    except Exception as _bex:
                        logger.debug(
                            "debug_supervisor_alert_send_failed sid=%s error=%s",
                            _sid_key, _bex,
                        )
                logger.info(
                    "__debug_inject_supervisor_alert dispatched payload=%s",
                    _payload,
                )
                await ws.send_json({
                    "type": "__debug_inject_ack",
                    "payload": {"ok": True},
                })

            else:
                await ws.send_json({
                    "type": "error",
                    "payload": {"message": f"unknown type: {msg_type}"},
                })

    except WebSocketDisconnect:
        # P4-S20: only clear the dict entry if it's still pointing at
        # OUR ws — a later connection may have replaced us already.
        if _control_connections.get(session_id) is ws:
            _control_connections.pop(session_id, None)
        logger.info("control channel disconnected", session_id=session_id)


@app.websocket("/ws/audio")
async def audio_channel(ws: WebSocket):
    await ws.accept()
    if not _validate_secret(ws):
        try:
            await ws.close(code=4001, reason="invalid secret")
        except Exception:
            pass
        return

    session_id = ws.query_params.get("session_id", "default")
    control_ws = _control_connections.get(session_id)

    from pipeline.voice_pipeline import VoicePipeline

    # Each audio connection gets its own VAD instance (stateful)
    session_vad = SileroVAD(
        threshold=config.vad.threshold,
        min_speech_ms=config.vad.min_speech_ms,
        min_silence_ms=config.vad.min_silence_ms,
    )
    await session_vad.load()

    # V5 §2.3 + S1: voice pipeline routes through agent_engine (not llm directly)
    # so that S2 memory / S3 tools flow uniformly through voice and text paths.
    # P4-S21 #13: also pass the v2 tool stack (registry + permission gate +
    # raw LLM provider) so voice utterances run through AgentLoop and can
    # actually invoke tools — same code path text chat already uses.
    pipeline = VoicePipeline(
        vad=session_vad,
        asr=service_context.asr_engine,
        agent=service_context.agent_engine,
        tts=service_context.tts_engine,
        control_ws=control_ws,
        session_id=session_id,
        vad_threshold_during_tts=config.voice.vad_threshold_during_tts,
        min_speech_ms_during_tts=config.voice.min_speech_ms_during_tts,
        tts_cooldown_ms=config.voice.tts_cooldown_ms,
        service_context=service_context,
        tool_registry_v2=deskpet_tool_registry_v2,
        permission_gate_v2=permission_gate_v2,
        local_llm=local_llm,
    )
    # Register so control-channel `interrupt` messages can reach us.
    _pipelines[session_id] = pipeline

    logger.info("audio channel connected", session_id=session_id)
    try:
        while True:
            data = await ws.receive_bytes()
            await pipeline.process_audio_chunk(data, ws)
    except WebSocketDisconnect:
        logger.info("audio channel disconnected", session_id=session_id)
    finally:
        _pipelines.pop(session_id, None)


def main():
    logger.info("starting backend", host=config.backend.host, port=config.backend.port)
    print(f"SHARED_SECRET={SHARED_SECRET}", flush=True)
    uvicorn.run(app, host=config.backend.host, port=config.backend.port, log_level=config.backend.log_level.lower())


if __name__ == "__main__":
    main()
