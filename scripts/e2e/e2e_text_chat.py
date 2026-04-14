"""E2E text chat smoke — full stack, real Ollama.

Simulates what App.tsx does on the control channel:
- open /ws/control
- send {type: chat, payload: {text: ...}}
- wait for chat_response
Reports latency + first 200 chars so we can eyeball quality.

Runs against a DEV_MODE=1 backend (secret ignored). For prod mode,
set --secret explicitly.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

import websockets


PROMPTS = [
    "你好！请简短地介绍一下你自己。",
    "现在几点？请调用工具查询。",  # exercises get_time tool
    "用一句话告诉我刚才我问了什么。",  # exercises memory read
]


async def chat_once(secret: str, session: str, text: str) -> tuple[float, str]:
    url = f"ws://127.0.0.1:8100/ws/control?secret={secret}&session_id={session}"
    start = time.perf_counter()
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"type": "chat", "payload": {"text": text}}))
        msg = await asyncio.wait_for(ws.recv(), timeout=60.0)
    data = json.loads(msg)
    elapsed = time.perf_counter() - start
    assert data["type"] == "chat_response", data
    return elapsed, data["payload"]["text"]


async def main(secret: str):
    session = f"e2e_chat_{int(time.time())}"
    print(f"[e2e] session={session}")
    for i, prompt in enumerate(PROMPTS, 1):
        print(f"\n[turn {i}] USER: {prompt}")
        dt, reply = await chat_once(secret, session, prompt)
        print(f"[turn {i}] BOT  ({dt*1000:.0f}ms): {reply[:200]}")
        if len(reply) > 200:
            print(f"[turn {i}]   ...(+{len(reply)-200} chars)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--secret", default="dev", help="ignored if backend is DEV_MODE")
    args = p.parse_args()
    asyncio.run(main(args.secret))
