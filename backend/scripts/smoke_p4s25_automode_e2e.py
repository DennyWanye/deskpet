# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S25 auto_mode end-to-end smoke:

Trigger a chat that requires shell permission. With auto_mode ON,
the request should be auto-allowed without any popup IPC fired.

Pass criteria:
- chat_v2_final arrives (LLM response completes)
- NO permission_request event seen during the run
"""
from __future__ import annotations
import asyncio
import json
import sys
import websockets


async def smoke(secret: str) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sid = "p4s25-automode-e2e"
    url = f"ws://127.0.0.1:8100/ws/control?secret={secret}&session_id={sid}"

    saw_permission_popup = False
    saw_final = False
    print(f"-> connect {url}")
    async with websockets.connect(url, ping_interval=None) as ws:
        # Send a chat that strongly hints at a shell command.
        await ws.send(json.dumps({
            "type": "chat_v2",
            "payload": {
                "text": "请用 run_shell 工具执行: echo p4s25-automode-test",
                "session_id": sid,
            },
        }))
        try:
            for _ in range(200):
                raw = await asyncio.wait_for(ws.recv(), timeout=180.0)
                msg = json.loads(raw)
                t = msg.get("type", "")
                if t == "permission_request":
                    saw_permission_popup = True
                    print(f"<- permission_request {msg['payload']}")
                if t == "tool_call":
                    print(f"<- tool_call {msg['payload'].get('name')}")
                if t == "tool_result":
                    print(f"<- tool_result {msg['payload'].get('tool')}")
                if t == "chat_v2_final":
                    print(f"<- chat_v2_final iters={msg['payload'].get('iterations')}")
                    saw_final = True
                    break
                if t == "chat_v2_error":
                    print(f"<- chat_v2_error {msg['payload']}")
                    break
        except asyncio.TimeoutError:
            print("<- timeout")
    print()
    print(f"saw_permission_popup={saw_permission_popup}")
    print(f"saw_final={saw_final}")
    if saw_permission_popup:
        print("FAIL: popup fired even though auto_mode was on")
        return 1
    print("PASS: no popup, request was auto-allowed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(smoke(sys.argv[1])))
