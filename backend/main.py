from __future__ import annotations

import sys
# Force UTF-8 stdout on Windows (default GBK chokes on emoji in LLM output)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

import os
import secrets
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pathlib import Path

from config import load_config
from context import ServiceContext

logger = structlog.get_logger()

PROJECT_ROOT = Path(__file__).parent.parent
config = load_config(PROJECT_ROOT / "config.toml")
SHARED_SECRET = secrets.token_hex(16)

service_context = ServiceContext()

# --- Register providers ---
from providers.ollama_llm import OllamaLLM
from providers.silero_vad import SileroVAD
from providers.faster_whisper_asr import FasterWhisperASR
from providers.edge_tts_provider import EdgeTTSProvider
from agent.providers.simple_llm import SimpleLLMAgent

ollama_llm = OllamaLLM(
    model=config.llm.model,
    base_url=config.llm.base_url,
    temperature=config.llm.temperature,
)
service_context.register("llm_engine", ollama_llm)

# V5 §2.3: agent_engine 与 llm_engine 分层。当前 SimpleLLMAgent 纯代理;
# S2 接记忆、S3 接工具路由都在 Agent 层扩展, 不动 WebSocket 层。
agent = SimpleLLMAgent(ollama_llm)
service_context.register("agent_engine", agent)

vad = SileroVAD(
    threshold=config.vad.threshold,
    min_speech_ms=config.vad.min_speech_ms,
    min_silence_ms=config.vad.min_silence_ms,
)
service_context.register("vad_engine", vad)

asr = FasterWhisperASR(
    model=config.asr.model,
    device=config.asr.device,
    compute_type=config.asr.compute_type,
    local_dir=str(Path(__file__).parent / "assets" / "faster-whisper-large-v3-turbo"),
)
service_context.register("asr_engine", asr)

tts = EdgeTTSProvider(voice=config.tts.voice)
service_context.register("tts_engine", tts)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Preload models on startup (best-effort — failures logged but don't block)."""
    logger.info("preloading models...")
    for name in ("vad_engine", "asr_engine", "tts_engine"):
        engine = service_context.get(name)
        if engine and hasattr(engine, "load"):
            try:
                await engine.load()
                logger.info("loaded", engine=name)
            except Exception as exc:
                logger.warning("failed_to_load", engine=name, error=str(exc))
    logger.info("startup complete")
    yield
    logger.info("shutting down")


app = FastAPI(title="Desktop Pet Backend", version="0.2.0", lifespan=lifespan)

# Track control channel connections for lip-sync forwarding
_control_connections: dict[str, WebSocket] = {}


# Opt-in dev mode: set DESKPET_DEV_MODE=1 to bypass shared-secret auth.
# Defaults to strict (secret required) so prod deployments are safe.
DEV_MODE = os.getenv("DESKPET_DEV_MODE", "0") == "1"

def _validate_secret(ws: WebSocket) -> bool:
    if DEV_MODE:
        return True
    secret = ws.headers.get("x-shared-secret", "")
    if not secret:
        secret = ws.query_params.get("secret", "")
    return secrets.compare_digest(secret, SHARED_SECRET)


@app.get("/health")
async def health():
    return {"status": "ok", "secret_hint": SHARED_SECRET[:4] + "..."}


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
    try:
        while True:
            raw = await ws.receive_json()
            msg_type = raw.get("type", "")

            if msg_type == "ping":
                await ws.send_json({"type": "pong"})

            elif msg_type == "chat":
                text = raw.get("payload", {}).get("text", "")
                response_text = f"[echo] {text}"

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
                    except Exception as exc:
                        logger.warning("agent_stream_failed", error=str(exc))
                        response_text = f"[echo] {text}"

                await ws.send_json({
                    "type": "chat_response",
                    "payload": {"text": response_text},
                })

            elif msg_type == "interrupt":
                logger.info("interrupt received")
                await ws.send_json({"type": "interrupt_ack"})

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
    )

    logger.info("audio channel connected", session_id=session_id)
    try:
        while True:
            data = await ws.receive_bytes()
            await pipeline.process_audio_chunk(data, ws)
    except WebSocketDisconnect:
        logger.info("audio channel disconnected", session_id=session_id)


def main():
    logger.info("starting backend", host=config.backend.host, port=config.backend.port)
    print(f"SHARED_SECRET={SHARED_SECRET}", flush=True)
    uvicorn.run(app, host=config.backend.host, port=config.backend.port, log_level=config.backend.log_level.lower())


if __name__ == "__main__":
    main()
