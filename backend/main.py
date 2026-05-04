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

local_llm = OpenAICompatibleProvider(
    base_url=config.llm.local.base_url,
    api_key=config.llm.local.api_key,
    model=config.llm.local.model,
    temperature=config.llm.local.temperature,
)

from config import resolve_cloud_api_key as _resolve_cloud_api_key  # P2-1-S3

_current_cloud_api_key: str | None = None

cloud_llm = None
if config.llm.cloud is not None:
    _cloud_key = _resolve_cloud_api_key()
    if _cloud_key:
        _current_cloud_api_key = _cloud_key
        cloud_llm = OpenAICompatibleProvider(
            base_url=config.llm.cloud.base_url,
            api_key=_cloud_key,
            model=config.llm.cloud.model,
            temperature=config.llm.cloud.temperature,
        )
    else:
        # No env var = user hasn't saved a key yet. Local-only is a
        # perfectly valid mode; don't spam the user at ERROR.
        logger.info(
            "cloud_llm_skipped",
            reason="DESKPET_CLOUD_API_KEY env not set — cloud provider disabled",
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
    # Module-level globals (service_context has a pre-declared key list
    # we don't want to extend just for this).
    # Accessed by the chat handler when it instantiates AgentLoop.
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
    _memory_manager = _MemoryManager(
        file_memory=_file_memory,
        session_db=_session_db,
        retriever=_retriever,
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
except Exception as _p4_exc:
    # S13 stay-alive guarantee: ANY P4 import/init failure must not block the
    # legacy chat path. p4_ipc.py already handles None services gracefully.
    logger.warning(
        "p4_services_registration_failed",
        error=str(_p4_exc),
        error_type=type(_p4_exc).__name__,
    )


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
        _mcp_manager = await _mcp_bootstrap(
            app_config=config.raw,
            tool_registry=service_context.get("tool_router"),
        )
        service_context.register("mcp_manager", _mcp_manager)
        logger.info("p4_mcp_manager_ready", states=_mcp_manager.server_state())
    except Exception as exc:
        logger.warning("p4_mcp_manager_bootstrap_failed", error=str(exc))
    logger.info("startup complete")
    yield
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
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if not (10 <= len(v) <= 256):
            raise ValueError("api_key length must be between 10 and 256 characters")
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
    """Hot-swap the cloud LLM provider at runtime (P2-1 UI slice).

    Auth: same shared-secret gate as /metrics. In DEV_MODE the gate is open
    so local smoke scripts can test without juggling headers.
    """
    global _current_cloud_api_key

    if not DEV_MODE:
        secret = request.headers.get("x-shared-secret", "")
        if not secret or not secrets.compare_digest(secret, SHARED_SECRET):
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="config"'},
            )

    # Resolve the api_key: use provided key, fall back to current, or 400.
    resolved_key: str | None = body.api_key  # already stripped by validator
    if not resolved_key:
        if _current_cloud_api_key:
            resolved_key = _current_cloud_api_key
        else:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=400,
                content={"detail": "no api_key configured"},
            )

    # Reuse temperature from current cloud provider if available, else default.
    current_temperature = 0.7
    if llm._cloud is not None:
        current_temperature = getattr(llm._cloud, "temperature", 0.7)

    new_provider = OpenAICompatibleProvider(
        base_url=body.base_url,
        api_key=resolved_key,
        model=body.model,
        temperature=current_temperature,
    )

    llm.set_cloud_provider(new_provider)
    _current_cloud_api_key = resolved_key

    if body.strategy is not None:
        llm.set_strategy(RoutingStrategy(body.strategy))

    logger.info(
        "cloud_config_updated",
        base_url=body.base_url,
        model=body.model,
        # api_key intentionally NOT logged
    )

    return {
        "ok": True,
        "cloud_configured": True,
        "base_url": body.base_url,
        "model": body.model,
        "has_api_key": True,
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

            elif msg_type == "chat":
                text = raw.get("payload", {}).get("text", "")
                response_text = f"[echo] {text}"
                budget_exceeded = False
                budget_reason: str | None = None

                # P4-S14: per-turn ContextAssembler. Runs BEFORE chat_stream so
                # the bundle's frozen_system + memory_block + skill_prelude land
                # in the prompt, and the decision is recorded for ContextTrace.
                # Failures NEVER block the chat path — bundle stays None, the
                # message list falls back to legacy ``[{"role":"user", ...}]``.
                _bundle = None
                _assembler = service_context.get("context_assembler")
                if _assembler is not None and getattr(_assembler, "enabled", True):
                    try:
                        import time as _ti
                        _bundle = await _assembler.assemble(
                            user_message=text,
                            memory_manager=service_context.get("memory_manager"),
                            tool_registry=service_context.get("tool_router"),
                            skill_registry=service_context.get("skill_loader"),
                            mcp_manager=service_context.get("mcp_manager"),
                            session_id=session_id,
                        )
                        # Stamp wall-clock + session so ContextTracePanel can
                        # render a meaningful timeline. AssemblyDecisions
                        # already records assembly_latency_ms during assemble.
                        if _bundle is not None and _bundle.decisions is not None:
                            _bundle.decisions.timestamp = _ti.time()
                            _bundle.decisions.session_id = session_id
                    except Exception as exc:
                        logger.warning(
                            "p4_assembler_failed",
                            error=str(exc),
                            error_type=type(exc).__name__,
                        )
                        _bundle = None

                # If assembler succeeded, build a fully-shaped messages list;
                # otherwise fall back to the legacy shape.
                if _bundle is not None:
                    _msgs = _bundle.build_messages(user_message=text)
                else:
                    _msgs = [{"role": "user", "content": text}]

                # V5 §2.3: route through agent_engine (not llm_engine directly).
                # Keeps WS layer stable when S2/S3 add memory/tools to Agent.
                agent_engine = service_context.agent_engine
                if agent_engine:
                    try:
                        response_text = ""
                        async for token in agent_engine.chat_stream(
                            _msgs,
                            session_id=session_id,
                        ):
                            response_text += token
                    except LLMUnavailableError as exc:
                        # P2-1-S8: surface budget-denied refusals distinctly
                        # so the UI can toast "预算已用尽" instead of a
                        # generic failure. Any other LLMUnavailableError
                        # (cloud+local both dead) still degrades to echo.
                        # Reason rides on the exception itself now — no more
                        # racy instance attribute shared between requests.
                        reason = exc.budget_reason
                        logger.warning("llm_unavailable", error=str(exc), reason=reason)
                        response_text = f"[echo] {text}"
                        if reason is not None:
                            budget_exceeded = True
                            budget_reason = reason
                    except Exception as exc:
                        logger.warning("agent_stream_failed", error=str(exc))
                        response_text = f"[echo] {text}"

                # P4-S14: feedback decision so ContextTrace shows the final
                # response length on the same record. Best-effort — never
                # surface to the user.
                if _bundle is not None and _assembler is not None:
                    try:
                        _assembler.feedback(_bundle, final_response=response_text)
                    except Exception as exc:
                        logger.warning("p4_assembler_feedback_failed", error=str(exc))

                # P2-1-S8: on a successful stream, consult the underlying
                # providers' last_usage (set by OpenAICompatibleProvider) and
                # debit the ledger. We probe both local and cloud — whichever
                # actually served the request left its usage on that object.
                served_by: str | None = None
                if not budget_exceeded:
                    for route, provider in (
                        ("cloud", llm._cloud),
                        ("local", llm._local),
                    ):
                        if provider is None:
                            continue
                        usage = getattr(provider, "last_usage", None)
                        if not usage:
                            continue
                        served_by = route
                        try:
                            await billing_ledger.record(
                                provider=route,
                                model=getattr(provider, "model", "unknown"),
                                prompt_tokens=int(usage.get("prompt_tokens", 0)),
                                completion_tokens=int(usage.get("completion_tokens", 0)),
                            )
                        except Exception as exc:
                            logger.warning("billing_record_failed", error=str(exc))
                        # Clear so the next call doesn't double-debit this
                        # usage block if only one provider ran.
                        provider.last_usage = None
                        break

                payload: dict = {"text": response_text}
                if served_by:
                    payload["provider"] = served_by
                if budget_exceeded:
                    payload["budget_exceeded"] = True
                    if budget_reason:
                        payload["budget_reason"] = budget_reason
                await ws.send_json({
                    "type": "chat_response",
                    "payload": payload,
                })

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

            else:
                await ws.send_json({
                    "type": "error",
                    "payload": {"message": f"unknown type: {msg_type}"},
                })

    except WebSocketDisconnect:
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
