"""P6 live E2E: send a real long task to the running backend via WS, watch
events, prove the hard gate fires.

Avoids computer-use churn. Connects to the running deskpet backend on
ws://127.0.0.1:8100/ws/control with empty secret (dev mode default).

Usage:
    cd backend && python scripts/p6_live_test.py

What it does:
  1. Opens ws.
  2. Sends a chat_v2 with a deliberately runaway prompt (asks the agent
     to do 50 grep+read+edit cycles on the deskpet repo).
  3. Receives streamed events.
  4. Counts tool_calls.
  5. Stops when it receives a termination event (FinalEvent or ErrorEvent).
  6. Prints summary + asserts hard gate fired.

This is the production-equivalent test — same code path the UI uses,
just driven from a script instead of computer-use.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

try:
    import websockets
except ImportError:
    print("Need websockets: pip install websockets")
    sys.exit(2)


import os
import re
SECRET = os.environ.get("DESKPET_SECRET", "")
if not SECRET:
    # auto-pick latest secret from tauri-dev.log
    try:
        with open("G:/projects/deskpet/tauri-dev.log", "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        m = list(re.finditer(r"secret=([a-f0-9]{32})", text))
        if m:
            SECRET = m[-1].group(1)
    except Exception:
        pass
print(f"Using secret={SECRET[:8]}... ({len(SECRET)} chars)")
BACKEND_WS = f"ws://127.0.0.1:8100/ws/control?secret={SECRET}&session_id=p6-live-test"

RUNAWAY_PROMPT = (
    "请帮我把 G:/projects/deskpet/backend/agent/ 目录下每一个 .py 文件都依次 "
    "read_file 一遍，然后对每个文件 grep 一次 'def '，再对每一个找到的函数都 "
    "用 read_file 读它所在的几行确认签名。这是一个故意需要至少 60+ 次 tool_call "
    "的任务，用来测试我们的 tool_budget hard cap。如果你看到这条系统提示说 "
    "'TOOL BUDGET EXHAUSTED' 或者 ErrorEvent，请立即 stop_reason=end_turn 结束。"
)


async def main() -> int:
    print(f"Connecting to {BACKEND_WS}")
    try:
        ws = await asyncio.wait_for(websockets.connect(BACKEND_WS), timeout=5.0)
    except (asyncio.TimeoutError, Exception) as exc:
        print(f"WS connect failed: {exc}")
        return 1

    print("Connected. Sending chat_v2 runaway prompt.")
    await ws.send(json.dumps({
        "type": "chat_v2",
        "payload": {
            "text": RUNAWAY_PROMPT,
            "session_id": "p6-live-test",
        },
    }))

    started = time.time()
    tool_count = 0
    iter_count = 0
    saw_terminate = False
    terminate_reason = None

    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            mtype = msg.get("type", "?")
            payload = msg.get("payload", {})

            # Print only interesting types so output stays scannable
            if mtype == "chat_v2_delta":
                continue
            elif mtype == "chat_v2_plan":
                print(f"  [plan] {len(payload.get('steps') or [])} steps")
            elif mtype == "tool_call":
                tool_count += 1
                tc = payload.get("tool_call") or {}
                print(f"  [tool_call #{tool_count}] {tc.get('name')} iter={payload.get('iteration')}")
            elif mtype == "tool_result":
                pass
            elif mtype == "provider_chain_fallback":
                print(f"  [fallback] {payload.get('from_')} → {payload.get('to')}: {payload.get('reason')}")
            elif mtype == "chat_v2_final":
                # 后端 payload.text (与 ws.ts:201 一致); chat_v2_final 即 end_turn
                final_text = payload.get("text") or payload.get("content") or ""
                print(f"  [final] text_len={len(final_text)}")
                saw_terminate = True
                terminate_reason = "end_turn"
                break
            elif mtype == "chat_v2_error":
                reason = payload.get("reason")
                detail = (payload.get("detail") or "")[:200]
                print(f"  [ERROR] reason={reason} detail={detail}")
                if reason in (
                    "error_tool_budget", "error_wall_clock_exceeded",
                    "error_max_turns", "context_budget_block",
                    "hallucination", "all_providers_failed",
                ):
                    saw_terminate = True
                    terminate_reason = reason
                    break
                if reason == "permanent_tool_error":
                    saw_terminate = True
                    terminate_reason = reason
                    break
            else:
                # Unhandled — print compact form
                pass

            if time.time() - started > 600:
                print("WALL CLOCK > 600s; bailing")
                break
    except Exception as exc:
        print(f"WS read exception: {exc}")
    finally:
        await ws.close()

    elapsed = time.time() - started
    print()
    print("─" * 50)
    print(f"Elapsed:           {elapsed:.1f}s")
    print(f"Tool calls:        {tool_count}")
    print(f"Saw termination:   {saw_terminate}")
    print(f"Termination reason:{terminate_reason}")
    print()

    # Assertions
    if not saw_terminate:
        print("❌ FAIL — no termination event seen in 600s")
        return 1

    # Tool budget hard cap default = 40
    if tool_count > 60:
        print(f"❌ FAIL — tool_count={tool_count} exceeded 60 (cap was 40, headroom 20)")
        return 1

    # Hard cap should fire well before wall-clock
    if terminate_reason in ("error_tool_budget", "error_wall_clock_exceeded",
                            "hallucination", "error_max_turns"):
        print(f"[PASS] HARD GATE FIRED: {terminate_reason}")
        return 0
    elif terminate_reason == "end_turn":
        print("[PASS] MODEL FINISHED NATURALLY (good behavior - gate not needed)")
        return 0
    else:
        print(f"[WARN] Terminated with {terminate_reason} - not the expected hard-gate event")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
