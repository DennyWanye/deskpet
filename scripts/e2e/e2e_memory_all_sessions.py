"""E2E cross-session memory scope smoke (P2-0-S3).

Seeds turns from two distinct sessions, then verifies:
1. `memory_list` with scope=session returns ONLY that session's turns.
2. `memory_list` with scope=all returns turns from BOTH sessions.
3. `memory_clear` with scope=all wipes everything; scope=session leaves
   the other session untouched.

Each session gets its own WS connection because session_id is bound to
the connection's query param (see backend/main.py ~line 295).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from typing import Any

import websockets


async def send_recv(ws, msg: dict[str, Any]) -> dict[str, Any]:
    await ws.send(json.dumps(msg))
    raw = await asyncio.wait_for(ws.recv(), timeout=60.0)
    return json.loads(raw)


async def seed(ws, text: str) -> None:
    """Fire a chat message and wait for its chat_response."""
    resp = await send_recv(ws, {"type": "chat", "payload": {"text": text}})
    assert resp["type"] == "chat_response", resp


async def main(secret: str) -> None:
    t = int(time.time())
    sess_a = f"e2e_all_A_{t}"
    sess_b = f"e2e_all_B_{t}"
    base = "ws://127.0.0.1:8100/ws/control"

    print(f"[all] session_a={sess_a}")
    print(f"[all] session_b={sess_b}")

    # Seed session A: 2 user messages (→ 4 turns: user/assistant/user/assistant)
    async with websockets.connect(
        f"{base}?secret={secret}&session_id={sess_a}"
    ) as ws_a:
        print("\n[all] seeding session A (2 chats)...")
        await seed(ws_a, "A-one")
        await seed(ws_a, "A-two")

        # Sanity: scope=session on A should see only A's turns
        resp = await send_recv(
            ws_a,
            {"type": "memory_list", "payload": {"scope": "session", "session_id": sess_a}},
        )
        turns_a = resp["payload"]["turns"]
        assert all(t["session_id"] == sess_a for t in turns_a), (
            f"session A leaked turns from other sessions: {turns_a}"
        )
        assert len(turns_a) == 4, f"expected 4 turns in A, got {len(turns_a)}"
        print(f"[all]   session A isolated: {len(turns_a)} turns")

    # Seed session B: 1 chat (→ 2 turns)
    async with websockets.connect(
        f"{base}?secret={secret}&session_id={sess_b}"
    ) as ws_b:
        print("\n[all] seeding session B (1 chat)...")
        await seed(ws_b, "B-one")

        # Step 1: scope=session on B should see ONLY B's turns (isolation)
        print("\n[all] step 1: memory_list scope=session (on B)")
        resp = await send_recv(
            ws_b,
            {"type": "memory_list", "payload": {"scope": "session", "session_id": sess_b}},
        )
        turns_b_only = resp["payload"]["turns"]
        assert all(t["session_id"] == sess_b for t in turns_b_only), (
            f"session B saw non-B turns: {turns_b_only}"
        )
        assert len(turns_b_only) == 2, f"expected 2 turns in B, got {len(turns_b_only)}"
        print(f"[all]   B-only: {len(turns_b_only)} turns (isolated OK)")

        # Step 2: scope=all should see turns from BOTH A and B
        print("\n[all] step 2: memory_list scope=all")
        resp = await send_recv(
            ws_b,
            {"type": "memory_list", "payload": {"scope": "all", "session_id": None}},
        )
        turns_all = resp["payload"]["turns"]
        sessions_in_all = {t["session_id"] for t in turns_all}
        assert sess_a in sessions_in_all, (
            f"scope=all missed session A ({sess_a}); saw: {sessions_in_all}"
        )
        assert sess_b in sessions_in_all, (
            f"scope=all missed session B ({sess_b}); saw: {sessions_in_all}"
        )
        # At minimum A (4) + B (2) = 6 — DB may hold other sessions from
        # prior test runs, so use >= not ==.
        my_turn_count = sum(1 for t in turns_all if t["session_id"] in (sess_a, sess_b))
        assert my_turn_count == 6, (
            f"expected 4 (A) + 2 (B) = 6 turns of ours in scope=all, got {my_turn_count}"
        )
        print(
            f"[all]   scope=all: {len(turns_all)} total turns across "
            f"{len(sessions_in_all)} session(s); ours: {my_turn_count} "
            f"(A={sess_a}, B={sess_b}) OK"
        )

        # Step 3: memory_clear scope=session on B should leave A intact
        print("\n[all] step 3: memory_clear scope=session (B)")
        resp = await send_recv(
            ws_b,
            {"type": "memory_clear", "payload": {"scope": "session", "session_id": sess_b}},
        )
        assert resp["type"] == "memory_clear_ack", resp
        assert resp["payload"]["scope"] == "session"

        # Verify: scope=all still sees A's turns but none of B's
        resp = await send_recv(
            ws_b,
            {"type": "memory_list", "payload": {"scope": "all", "session_id": None}},
        )
        turns_after = resp["payload"]["turns"]
        remaining_b = [t for t in turns_after if t["session_id"] == sess_b]
        remaining_a = [t for t in turns_after if t["session_id"] == sess_a]
        assert not remaining_b, f"B should be empty after session-clear, got {remaining_b}"
        assert len(remaining_a) == 4, (
            f"A should be intact (4 turns) after B-session-clear, got {len(remaining_a)}"
        )
        print(
            f"[all]   after clear-B: A has {len(remaining_a)} turns (intact), "
            f"B has {len(remaining_b)} turns (empty) OK"
        )

        # Step 4: memory_clear scope=all — nukes A too
        print("\n[all] step 4: memory_clear scope=all")
        resp = await send_recv(
            ws_b, {"type": "memory_clear", "payload": {"scope": "all"}}
        )
        assert resp["type"] == "memory_clear_ack", resp
        assert resp["payload"]["scope"] == "all"
        removed = resp["payload"].get("removed", 0)
        print(f"[all]   clear-all removed {removed} turns")

        # Final: scope=all should report zero turns for our sessions
        resp = await send_recv(
            ws_b,
            {"type": "memory_list", "payload": {"scope": "all", "session_id": None}},
        )
        final_ours = [
            t for t in resp["payload"]["turns"] if t["session_id"] in (sess_a, sess_b)
        ]
        assert not final_ours, f"clear-all left turns behind: {final_ours}"
        print(f"[all]   final: 0 turns for sessions A+B OK")

    print("\n[all] ALL PASS")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--secret", default="dev")
    args = p.parse_args()
    asyncio.run(main(args.secret))
