"""E2E memory management API smoke.

Simulates MemoryPanel.tsx operations over the control WS:
1. chat 2 turns to seed memory
2. memory_list → expect 4 turns (2 user + 2 assistant)
3. memory_delete one turn → confirm removed
4. memory_export → confirm JSON shape
5. memory_clear (scope=session) → confirm empty
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

import websockets


async def send_recv(ws, msg):
    await ws.send(json.dumps(msg))
    raw = await asyncio.wait_for(ws.recv(), timeout=60.0)
    return json.loads(raw)


async def main(secret: str):
    session = f"e2e_mem_{int(time.time())}"
    url = f"ws://127.0.0.1:8100/ws/control?secret={secret}&session_id={session}"

    print(f"[mem] session={session}")
    async with websockets.connect(url) as ws:
        # Seed — two chat turns so both user + assistant get stored.
        print("[mem] seeding 2 chat turns...")
        r1 = await send_recv(ws, {"type": "chat", "payload": {"text": "第一条消息"}})
        assert r1["type"] == "chat_response"
        r2 = await send_recv(ws, {"type": "chat", "payload": {"text": "第二条消息"}})
        assert r2["type"] == "chat_response"

        # 1. List
        print("\n[mem] step 1: memory_list")
        resp = await send_recv(ws, {"type": "memory_list", "payload": {}})
        assert resp["type"] == "memory_list_response", resp
        turns = resp["payload"]["turns"]
        print(f"[mem]   got {len(turns)} turns:")
        for t in turns:
            print(f"[mem]     id={t['id']}  {t['role']:9s}  {t['content'][:50]}")
        assert len(turns) == 4, f"expected 4 turns, got {len(turns)}"
        roles = [t["role"] for t in turns]
        assert roles == ["user", "assistant", "user", "assistant"], roles

        # 2. Delete one
        victim = turns[1]  # first assistant reply
        print(f"\n[mem] step 2: memory_delete id={victim['id']}")
        resp = await send_recv(
            ws, {"type": "memory_delete", "payload": {"id": victim["id"]}}
        )
        assert resp["type"] == "memory_delete_ack", resp
        assert resp["payload"]["deleted"] is True
        print(f"[mem]   deleted ack: {resp['payload']}")

        # Re-list to confirm
        resp = await send_recv(ws, {"type": "memory_list", "payload": {}})
        new_ids = [t["id"] for t in resp["payload"]["turns"]]
        assert victim["id"] not in new_ids, "victim still present"
        assert len(resp["payload"]["turns"]) == 3
        print(f"[mem]   confirmed: 3 turns remain")

        # 3. Export
        print("\n[mem] step 3: memory_export")
        resp = await send_recv(ws, {"type": "memory_export", "payload": {}})
        assert resp["type"] == "memory_export_response", resp
        exported = resp["payload"]
        print(f"[mem]   exported_at={exported['exported_at']:.1f}")
        print(f"[mem]   sessions={len(exported['sessions'])}  turns={len(exported['turns'])}")
        # Our session must be in there
        my_sessions = [s for s in exported["sessions"] if s["session_id"] == session]
        assert len(my_sessions) == 1, my_sessions
        assert my_sessions[0]["turn_count"] == 3

        # 4. Clear this session
        print("\n[mem] step 4: memory_clear (session scope)")
        resp = await send_recv(ws, {"type": "memory_clear", "payload": {}})
        assert resp["type"] == "memory_clear_ack", resp
        print(f"[mem]   clear ack: {resp['payload']}")

        # Re-list — should be empty
        resp = await send_recv(ws, {"type": "memory_list", "payload": {}})
        assert resp["payload"]["turns"] == [], resp["payload"]
        print(f"[mem]   confirmed: session now empty")

    print("\n[mem] ALL PASS")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--secret", default="dev")
    args = p.parse_args()
    asyncio.run(main(args.secret))
