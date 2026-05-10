"""P4-S24 manual smoke #2: send a chat to the user's REAL `default`
session, which has assistant rows persisted before the reasoning_content
fix landed. Without the retry shim, this would fail with HTTP 400.

Usage: python scripts/smoke_p4s24_stale_session.py <secret>
"""
from __future__ import annotations

import asyncio
import json
import sys

import websockets


async def smoke(secret: str) -> int:
    chat_sid = "code-px2ifh8e"  # has 1 stale assistant row (no reasoning)
    # Use a unique WS-level session_id so the pet's existing default
    # conn doesn't get evicted. The chat_v2 payload's session_id is
    # what selects the SessionDB rows.
    ws_sid = "p4s24-stale-test"
    url = (
        f"ws://127.0.0.1:8100/ws/control?secret={secret}"
        f"&session_id={ws_sid}"
    )
    saw_400 = False
    saw_retry = False
    print(f"-> connect on ws_sid={ws_sid}, target chat_sid={chat_sid} (stale rows)")
    async with websockets.connect(url, ping_interval=None) as ws:
        await ws.send(json.dumps({
            "type": "chat_v2",
            "payload": {
                "text": "用一句话告诉我现在几点。",
                "session_id": chat_sid,
            },
        }))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=180.0)
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            t = msg.get("type", "")
            if t == "chat_v2_final":
                text = msg["payload"].get("text", "")
                print(f"<- chat_v2_final iterations={msg['payload'].get('iterations')} text={text[:60]!r}")
                break
            if t == "chat_v2_error":
                detail = msg["payload"].get("detail", "")
                print(f"<- chat_v2_error reason={msg['payload'].get('reason')} detail={detail[:200]}")
                if "HTTP 400" in detail or "reasoning_content" in detail:
                    saw_400 = True
                break
            if t == "tool_call":
                print(f"<- tool_call {msg['payload'].get('name')}")
            if t == "tool_result":
                print(f"<- tool_result {msg['payload'].get('tool')}")
    print()
    print(f"Result: saw_400={saw_400}")
    return 1 if saw_400 else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(smoke(sys.argv[1])))
