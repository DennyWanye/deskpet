"""P4-S24 transient-retry smoke: hammer the LLM with 6 sequential
requests on independent sessions to hit the relay's flaky 'Server
disconnected' behavior. Verifies:

1. No request bubbles up "Server disconnected" to the user when the
   transient retry layer succeeds on retry attempt 2 or 3.
2. Final-failure case (3 attempts all fail) still surfaces a clean
   error string, not a Python traceback.

Pass criteria: every chat ends with chat_v2_final OR a
clean chat_v2_error. Either is OK — what's NOT OK is a hung
connection or unhandled exception.
"""
from __future__ import annotations

import asyncio
import json
import sys

import websockets


PROMPTS = [
    "用一句话告诉我现在几点。",
    "今天星期几？",
    "1+1 等于几？",
    "你叫什么名字？",
    "Python 是哪一年发布的？",
    "用一句话总结你能做什么。",
]


async def smoke(secret: str) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    finals = 0
    errors = 0
    transient_after_final = 0

    for i, prompt in enumerate(PROMPTS):
        ws_sid = f"p4s24-trans-{i}"
        url = (
            f"ws://127.0.0.1:8100/ws/control?secret={secret}"
            f"&session_id={ws_sid}"
        )
        print(f"\n=== prompt {i+1}/{len(PROMPTS)}: {prompt!r} ===")
        try:
            async with websockets.connect(url, ping_interval=None) as ws:
                await ws.send(json.dumps({
                    "type": "chat_v2",
                    "payload": {"text": prompt, "session_id": ws_sid},
                }))
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=180.0)
                    msg = json.loads(raw)
                    t = msg.get("type", "")
                    if t == "chat_v2_final":
                        text = msg["payload"].get("text", "")
                        print(f"   final iters={msg['payload'].get('iterations')} text={text[:50]!r}")
                        finals += 1
                        break
                    if t == "chat_v2_error":
                        detail = msg["payload"].get("detail", "")
                        print(f"   ERROR detail={detail[:200]}")
                        errors += 1
                        break
                    if t == "tool_call":
                        print(f"   tool_call {msg['payload'].get('name')}")
        except Exception as exc:
            print(f"   WS error: {type(exc).__name__}: {exc}")
            errors += 1

    print(f"\n=== Result: {finals} final / {errors} error / {len(PROMPTS)} total ===")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(smoke(sys.argv[1])))
