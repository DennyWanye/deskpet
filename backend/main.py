from __future__ import annotations

import secrets
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

from providers.ollama_llm import OllamaLLM

ollama_llm = OllamaLLM(
    model=config.llm.model,
    base_url=config.llm.base_url,
    temperature=config.llm.temperature,
)
service_context.register("llm_engine", ollama_llm)

app = FastAPI(title="Desktop Pet Backend", version="0.1.0")


def _validate_secret(ws: WebSocket) -> bool:
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
        await ws.close(code=4001, reason="invalid secret")
        return

    logger.info("control channel connected")
    try:
        while True:
            raw = await ws.receive_json()
            msg_type = raw.get("type", "")

            if msg_type == "ping":
                await ws.send_json({"type": "pong"})

            elif msg_type == "chat":
                text = raw.get("payload", {}).get("text", "")
                response_text = f"[echo] {text}"

                llm = service_context.llm_engine
                if llm:
                    try:
                        response_text = ""
                        async for token in llm.chat_stream(
                            [{"role": "user", "content": text}]
                        ):
                            response_text += token
                    except Exception as exc:
                        logger.warning("llm_stream_failed", error=str(exc))
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
        logger.info("control channel disconnected")


@app.websocket("/ws/audio")
async def audio_channel(ws: WebSocket):
    await ws.accept()
    if not _validate_secret(ws):
        await ws.close(code=4001, reason="invalid secret")
        return

    logger.info("audio channel connected")
    try:
        while True:
            data = await ws.receive_bytes()
            logger.info("audio received", size=len(data))
            await ws.send_json({
                "type": "audio_ack",
                "payload": {"received_bytes": len(data)},
            })
    except WebSocketDisconnect:
        logger.info("audio channel disconnected")


def main():
    logger.info("starting backend", host=config.backend.host, port=config.backend.port)
    print(f"SHARED_SECRET={SHARED_SECRET}", flush=True)
    uvicorn.run(app, host=config.backend.host, port=config.backend.port, log_level=config.backend.log_level.lower())


if __name__ == "__main__":
    main()
