from __future__ import annotations
import secrets
import structlog
import uvicorn
from fastapi import FastAPI
from pathlib import Path
from config import load_config

logger = structlog.get_logger()
PROJECT_ROOT = Path(__file__).parent.parent
config = load_config(PROJECT_ROOT / "config.toml")
SHARED_SECRET = secrets.token_hex(16)

app = FastAPI(title="Desktop Pet Backend", version="0.1.0")

@app.get("/health")
async def health():
    return {"status": "ok", "secret_hint": SHARED_SECRET[:4] + "..."}

def main():
    logger.info("starting backend", host=config.backend.host, port=config.backend.port)
    print(f"SHARED_SECRET={SHARED_SECRET}")
    uvicorn.run(app, host=config.backend.host, port=config.backend.port, log_level=config.backend.log_level.lower())

if __name__ == "__main__":
    main()
