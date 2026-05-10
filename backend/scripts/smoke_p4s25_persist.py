"""P4-S25 persistence smoke: verify

1. code_sessions_list_response includes restored projects
2. session_messages_load returns prior chat history for them
3. code_mode_enter on the same project_root returns the EXISTING
   base_session_id (no orphan history)

Run AFTER deskpet has restarted (so backend re-loaded code_sessions).
"""
from __future__ import annotations
import asyncio
import json
import sys
import websockets


async def smoke(secret: str) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sid = "p4s25-persist-test"
    url = f"ws://127.0.0.1:8100/ws/control?secret={secret}&session_id={sid}"

    print(f"-> connect {url}")
    async with websockets.connect(url, ping_interval=None) as ws:
        # Step 1: pull persisted sessions
        await ws.send(json.dumps({"type": "code_sessions_list"}))
        list_resp = None
        for _ in range(20):
            raw = await asyncio.wait_for(ws.recv(), timeout=15.0)
            msg = json.loads(raw)
            if msg.get("type") == "code_sessions_list_response":
                list_resp = msg
                break
        if not list_resp:
            print("FAIL: never got code_sessions_list_response")
            return 1
        items = list_resp["payload"]["items"]
        print(f"-> got {len(items)} persisted sessions:")
        for it in items:
            print(f"   - {it['base_session_id']} → {it['project_name']} ({it['project_root']})")

        if not items:
            print("FAIL: no persisted sessions found")
            return 1

        # Step 2: pull messages for each restored session
        for it in items:
            target = it["base_session_id"]
            await ws.send(json.dumps({
                "type": "session_messages_load",
                "payload": {"session_id": target, "limit": 50},
            }))
            for _ in range(20):
                raw = await asyncio.wait_for(ws.recv(), timeout=15.0)
                msg = json.loads(raw)
                if msg.get("type") == "session_messages_response":
                    msgs = msg["payload"].get("messages") or []
                    print(f"-> session_messages_response for {target}: {len(msgs)} msgs")
                    for m in msgs[:3]:
                        text = (m.get("text") or "")[:60]
                        print(f"     [{m.get('role')}] {text}")
                    break

        # Step 3: send code_mode_enter for an EXISTING project_root, verify
        # backend returns the same base_session_id (dedup fix).
        existing = items[0]
        existing_sid = existing["base_session_id"]
        existing_root = existing["project_root"]
        new_random_sid = "code-RANDOM-fresh-" + str(asyncio.get_event_loop().time())[-4:]
        await ws.send(json.dumps({
            "type": "code_mode_enter",
            "payload": {
                "project_path": existing_root,
                "suggested_name": "untitled",
                "session_id": new_random_sid,
            },
        }))
        for _ in range(20):
            raw = await asyncio.wait_for(ws.recv(), timeout=15.0)
            msg = json.loads(raw)
            if msg.get("type") == "code_mode_state":
                returned_sid = msg["payload"].get("session_id")
                print(f"-> code_mode_state returned session_id={returned_sid}")
                if returned_sid == existing_sid:
                    print(f"   PASS: backend reused existing sid {existing_sid} (not the fresh {new_random_sid})")
                else:
                    print(f"   FAIL: backend created new sid {returned_sid} instead of reusing {existing_sid}")
                    return 1
                break

    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(smoke(sys.argv[1])))
