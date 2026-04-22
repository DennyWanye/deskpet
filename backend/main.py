from __future__ import annotations

import sys
# Force UTF-8 stdout on Windows (default GBK chokes on emoji in LLM output)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

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

from config import load_config
from paths import resolve_model_dir  # P3-S1
from context import ServiceContext
from observability.crash_reports import install_crash_reporter
from observability.metrics import render as render_metrics
from observability.startup import registry as startup_errors  # P3-S2

# Install the uncaught-exception hook as early as possible so import-time
# failures later in this file still land in crash_reports/.
install_crash_reporter()

logger = structlog.get_logger()

PROJECT_ROOT = Path(__file__).parent.parent
config = load_config(PROJECT_ROOT / "config.toml")
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
from memory.conversation import SqliteConversationMemory
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

# S2: memory store — short-term conversation history (SQLite).
# Path from config.toml; falls back to ./data/memory.db if unset.
# S6 (R13): wrap with RedactingMemoryStore so secrets/PII never hit disk.
raw_memory = SqliteConversationMemory(db_path=config.memory.db_path)
memory_store = RedactingMemoryStore(raw_memory)
service_context.register("memory_store", memory_store)

# V5 §2.3: agent_engine 与 llm_engine 分层。
# 组装栈:ToolUsingAgent(S3) 包装 SimpleLLMAgent(S2 + S0), memory 在内层。
# 工具调用的结果是 inline 注入 user-facing stream,不走 memory 持久化。
tool_registry = ToolRegistry()
tool_registry.register(get_time_tool)
tool_registry.register(read_clipboard_tool)
tool_registry.register(list_reminders_tool)
service_context.register("tool_router", tool_registry)

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

base_agent = SimpleLLMAgent(llm, memory=memory_store)
agent = ToolUsingAgent(base=base_agent, registry=tool_registry)
service_context.register("agent_engine", agent)

vad = SileroVAD(
    threshold=config.vad.threshold,
    min_speech_ms=config.vad.min_speech_ms,
    min_silence_ms=config.vad.min_silence_ms,
)
service_context.register("vad_engine", vad)

# P3-S5 hotfix: the frozen PyInstaller bundle strips torch's CUDA DLLs
# (cublas/cufft/cusparse/... ~2.9 GB) to meet the size budget. ctranslate2's
# faster-whisper GPU path still dlopen's cublas64_12.dll at first transcribe,
# so in frozen mode we must force CPU inference regardless of config. Dev
# mode (running main.py directly) keeps the user's cuda setting.
if getattr(sys, "frozen", False) and config.asr.device == "cuda":
    logger.warning(
        "asr_device_frozen_override",
        from_device=config.asr.device,
        from_compute=config.asr.compute_type,
        to_device="cpu",
        to_compute="int8",
        reason="frozen bundle strips CUDA DLLs; ctranslate2 GPU path would fail",
    )
    config.asr.device = "cpu"
    config.asr.compute_type = "int8"

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
    logger.info("startup complete")
    yield
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

            elif msg_type == "chat":
                text = raw.get("payload", {}).get("text", "")
                response_text = f"[echo] {text}"
                budget_exceeded = False
                budget_reason: str | None = None

                # V5 §2.3: route through agent_engine (not llm_engine directly).
                # Keeps WS layer stable when S2/S3 add memory/tools to Agent.
                agent_engine = service_context.agent_engine
                if agent_engine:
                    try:
                        response_text = ""
                        async for token in agent_engine.chat_stream(
                            [{"role": "user", "content": text}],
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
