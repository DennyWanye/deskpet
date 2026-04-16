"""Real-test variant of smoke_chat: writes UTF-8 output so the cloud
reply is legible on Windows (default cp936 mangles CJK)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import websockets


async def main() -> int:
    secret = os.environ.get("DESKPET_SHARED_SECRET", "")
    url = f"ws://127.0.0.1:8100/ws/control?secret={secret}&session_id=real-test-cloud"
    prompt = "你好。请用一句话告诉我你是哪个模型、由谁训练，不要说其他任何内容。"

    out = Path(__file__).parent.parent.parent / "temp" / "real_test_cloud_reply.txt"
    out.parent.mkdir(exist_ok=True)

    print(f"[rt] connecting: {url}")
    print(f"[rt] prompt: {prompt}")
    async with websockets.connect(url) as ws:
        payload = {"type": "chat", "payload": {"text": prompt}}
        t0 = time.perf_counter()
        await ws.send(json.dumps(payload))

        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=60.0)
            msg = json.loads(raw)
            if msg.get("type") == "chat_response":
                dt = time.perf_counter() - t0
                text = msg.get("payload", {}).get("text", "")
                out.write_text(
                    f"Prompt: {prompt}\n"
                    f"RTT:    {dt:.2f}s\n"
                    f"Chars:  {len(text)}\n"
                    f"Text:\n{text}\n",
                    encoding="utf-8",
                )
                print(f"[rt] chat_response received in {dt:.2f}s, {len(text)} chars")
                print(f"[rt] wrote to {out}")
                return 0
            else:
                print(f"[rt] ignoring: {msg.get('type')}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
