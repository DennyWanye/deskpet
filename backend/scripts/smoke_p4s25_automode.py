# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S25 auto_mode persistence smoke:

1. Send permission_auto_mode_set enabled=true via WS
2. Verify backend ack
3. Read the persistence file and confirm {"enabled": true}
4. Send permission_auto_mode_set enabled=false
5. Confirm file flips to {"enabled": false}
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import websockets


async def smoke(secret: str) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sid = "p4s25-automode-test"
    url = f"ws://127.0.0.1:8100/ws/control?secret={secret}&session_id={sid}"
    persist_path = os.path.join(
        os.environ["APPDATA"], "deskpet", "permissions_auto_mode.json"
    )

    print(f"-> persist file: {persist_path}")
    async with websockets.connect(url, ping_interval=None) as ws:
        # Toggle ON
        await ws.send(json.dumps({
            "type": "permission_auto_mode_set",
            "payload": {"enabled": True},
        }))
        for _ in range(20):
            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            msg = json.loads(raw)
            if msg.get("type") == "permission_auto_mode_response":
                print(f"<- ack enabled={msg['payload']['enabled']}")
                break
        if not os.path.exists(persist_path):
            print(f"FAIL: file not written")
            return 1
        with open(persist_path) as f:
            data = json.load(f)
        print(f"<- file: {data}")
        assert data["enabled"] is True, f"expected enabled=True, got {data}"

        # Toggle OFF
        await ws.send(json.dumps({
            "type": "permission_auto_mode_set",
            "payload": {"enabled": False},
        }))
        for _ in range(20):
            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            msg = json.loads(raw)
            if msg.get("type") == "permission_auto_mode_response":
                print(f"<- ack enabled={msg['payload']['enabled']}")
                break
        with open(persist_path) as f:
            data = json.load(f)
        print(f"<- file: {data}")
        assert data["enabled"] is False, f"expected enabled=False, got {data}"

        # Toggle back ON for the user (since they want it on)
        await ws.send(json.dumps({
            "type": "permission_auto_mode_set",
            "payload": {"enabled": True},
        }))
        for _ in range(20):
            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            msg = json.loads(raw)
            if msg.get("type") == "permission_auto_mode_response":
                print(f"<- final ack enabled={msg['payload']['enabled']}")
                break

    print("\nPASS — auto_mode persisted correctly")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(smoke(sys.argv[1])))
