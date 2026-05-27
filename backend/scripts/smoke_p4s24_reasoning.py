"""P4-S24 manual smoke: send 2 sequential chat_v2 turns through the
control WS and assert neither produces an LLM HTTP 400 (the
reasoning_content round-trip bug).

Usage:
    python scripts/smoke_p4s24_reasoning.py <shared_secret>

The shared secret is whatever the live deskpet backend printed at
startup; grep `tauri-dev*.log` for `secret=<32 hex>` (the WS connection
URL has it). This script does NOT depend on Tauri / keychain — works
against any running backend.

What it proves:
- Turn 1 succeeds (200) → reasoning_content extracted from response.
- Turn 2 succeeds (200) → prior assistant turn was rebuilt from
  SessionDB WITH `reasoning_content`, so the your-llm-relay.example.com endpoint
  doesn't 400 with "reasoning_content must be passed back".

If turn 2 fails with `chat_v2_error` carrying "HTTP 400", the round-trip
fix is broken — check the backend log for the
`p4s24_reasoning_extract` line to see whether reasoning_content was
extracted from turn 1's response and whether it ended up in turn 2's
outgoing message stack.
"""
from __future__ import annotations

import asyncio
import json
import sys

import websockets


async def smoke(secret: str) -> int:
    sid = "p4s24-smoke"
    url = (
        f"ws://127.0.0.1:8100/ws/control?secret={secret}"
        f"&session_id={sid}"
    )
    saw_400 = False
    saw_final_turn1 = False
    saw_final_turn2 = False
    print(f"-> connect {url}")
    async with websockets.connect(url, ping_interval=None) as ws:

        async def send_chat(text: str) -> None:
            await ws.send(json.dumps({
                "type": "chat_v2",
                "payload": {"text": text, "session_id": sid},
            }))

        async def drain_until_final() -> bool:
            """Read until chat_v2_final or chat_v2_error.

            Returns True on success (final), False on error (incl. 400).
            """
            nonlocal saw_400
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=180.0)
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                t = msg.get("type", "")
                if t == "chat_v2_final":
                    print(
                        f"   <- chat_v2_final iterations={msg['payload'].get('iterations')} "
                        f"text_chars={len(msg['payload'].get('text', ''))}"
                    )
                    return True
                if t == "chat_v2_error":
                    detail = msg["payload"].get("detail", "")
                    print(f"   <- chat_v2_error reason={msg['payload'].get('reason')} detail={detail[:200]}")
                    if "HTTP 400" in detail or "reasoning_content" in detail:
                        saw_400 = True
                    return False
                if t == "tool_call":
                    print(f"   <- tool_call name={msg['payload'].get('name')}")
                if t == "tool_result":
                    print(f"   <- tool_result tool={msg['payload'].get('tool')}")

        # --- Turn 1 ----------------------------------------------------
        print("\n=== Turn 1: send first message ===")
        await send_chat(
            "请用一句话告诉我现在几点（用 get_time 工具）。"
        )
        saw_final_turn1 = await drain_until_final()

        # --- Turn 2 (the critical one — would 400 before fix) ---------
        print("\n=== Turn 2: send second message ===")
        await send_chat(
            "好的，那再用一句话告诉我今天是星期几。"
        )
        saw_final_turn2 = await drain_until_final()

    print("\n=== Result ===")
    print(f"  turn 1 final  : {saw_final_turn1}")
    print(f"  turn 2 final  : {saw_final_turn2}")
    print(f"  saw 400 / reasoning_content error: {saw_400}")
    if saw_final_turn1 and saw_final_turn2 and not saw_400:
        print("  PASS — reasoning_content round-trip works")
        return 0
    print("  FAIL — multi-turn still broken")
    return 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(smoke(sys.argv[1])))
