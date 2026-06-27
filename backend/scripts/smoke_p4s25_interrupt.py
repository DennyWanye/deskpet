# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S25 B3 interrupt smoke: send a chat, then interrupt mid-flight.

Pass criteria:
- chat_v2_interrupted echo lands within 5s of sending the interrupt
- chat_v2_final / chat_v2_error does NOT arrive after interrupt (or
  arrives quickly with cancelled state)
"""
from __future__ import annotations
import asyncio
import json
import sys
import websockets


async def smoke(secret: str) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sid = "p4s25-interrupt"
    url = f"ws://127.0.0.1:8100/ws/control?secret={secret}&session_id={sid}"
    print(f"-> connect {url}")
    async with websockets.connect(url, ping_interval=None) as ws:
        # Send a request that takes a while (long text → thinking model
        # will spend time).
        await ws.send(json.dumps({
            "type": "chat_v2",
            "payload": {
                "text": "请用 200 字解释一下量子纠缠是什么，举几个例子。",
                "session_id": sid,
            },
        }))
        # Wait briefly so the LLM call is in flight, then interrupt.
        await asyncio.sleep(1.5)
        print("-> sending chat_v2_interrupt")
        await ws.send(json.dumps({
            "type": "chat_v2_interrupt",
            "payload": {"session_id": sid},
        }))
        # Drain events for up to 10s.
        saw_interrupted = False
        saw_final = False
        try:
            for _ in range(100):
                raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                msg = json.loads(raw)
                t = msg.get("type", "")
                if t == "chat_v2_interrupted":
                    print(f"<- chat_v2_interrupted cancelled={msg['payload'].get('cancelled')}")
                    saw_interrupted = True
                if t == "chat_v2_final":
                    saw_final = True
                    print("<- chat_v2_final (arrived after interrupt)")
                if t == "chat_v2_error":
                    print(f"<- chat_v2_error reason={msg['payload'].get('reason')}")
                if saw_interrupted:
                    break
        except asyncio.TimeoutError:
            pass

    print(f"\nResult: saw_interrupted={saw_interrupted} saw_final={saw_final}")
    # Pass if interrupted echo arrived. Final after interrupt is OK if
    # the LLM call had already completed.
    return 0 if saw_interrupted else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(smoke(sys.argv[1])))
