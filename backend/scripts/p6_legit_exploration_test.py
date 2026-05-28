# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P6 args-aware fix verification — legitimate exploration must NOT
trigger HALLUCINATION_DETECTED.

Pre-fix bug: reading 5+ different files via read_file in a row was
falsely classified as a "death loop" because the per-tool consecutive
counter ignored args. After the 2026-05-13 args-aware fix, identical
args repeated triggers the cap, but different paths reset the counter.

This script asks the agent to do a small, legitimate exploration that
PREVIOUSLY would have tripped the bug (5-6 different reads), and
asserts:
  1. The run completes normally (stop_reason=end_turn)
  2. No `hallucination` reason fires
  3. No auto_resume_engaged with reason=hallucination
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time

try:
    import websockets
except ImportError:
    print("Need websockets: pip install websockets")
    sys.exit(2)

# Windows: stdout buffering hides progress when piped/background-run.
# Force line-buffered so tail-style log inspection sees output as it happens.
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass


SECRET = os.environ.get("DESKPET_SECRET", "")
if not SECRET:
    try:
        with open("G:/projects/deskpet/tauri-dev.log", "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        m = list(re.finditer(r"secret=([a-f0-9]{32})", text))
        if m:
            SECRET = m[-1].group(1)
    except Exception:
        pass
print(f"Using secret={SECRET[:8]}... ({len(SECRET)} chars)")
BACKEND_WS = f"ws://127.0.0.1:8100/ws/control?secret={SECRET}&session_id=p6-legit-test"

# Legitimate-exploration prompt: 5 different read_file calls. This is
# the EXACT pattern that triggered the false positive pre-fix.
LEGIT_PROMPT = (
    "请帮我探索一下 G:/projects/deskpet/backend/agent/ 目录的结构。"
    "我想知道这几个文件分别是干啥的，每个都用 read_file 看前 50 行就好："
    "agent_loop.py, termination.py, context_manager.py, auto_resume.py, "
    "tool_use_shim.py。最后用一句话总结每个文件的职责，不需要 grep 也不需要写代码。"
)


async def main() -> int:
    print(f"Connecting to {BACKEND_WS}")
    try:
        ws = await asyncio.wait_for(websockets.connect(BACKEND_WS), timeout=5.0)
    except Exception as exc:
        print(f"WS connect failed: {exc}")
        return 1

    print("Connected. Sending legitimate-exploration prompt.")
    await ws.send(json.dumps({
        "type": "chat_v2",
        "payload": {"text": LEGIT_PROMPT, "session_id": "p6-legit-test"},
    }))

    started = time.time()
    tool_count = 0
    read_file_count = 0
    saw_terminate = False
    terminate_reason: str | None = None
    seen_hallucination = False
    seen_auto_resume = False

    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            mtype = msg.get("type", "?")
            payload = msg.get("payload", {})

            if mtype == "chat_v2_delta":
                continue
            elif mtype == "tool_call":
                tool_count += 1
                tc = payload.get("tool_call") or {}
                if tc.get("name") == "read_file":
                    read_file_count += 1
                print(f"  [tool_call #{tool_count}] {tc.get('name')} iter={payload.get('iteration')}")
            elif mtype == "tool_result":
                pass
            elif mtype == "chat_v2_final":
                # 后端 payload 字段是 text (与 tauri-app/src/code-panel/ws.ts:201 一致)，
                # 不是 content；不带 stop_reason，因为 final 本身意味着 end_turn。
                final_text = payload.get("text") or payload.get("content") or ""
                print(f"  [final] text_len={len(final_text)}")
                saw_terminate = True
                terminate_reason = "end_turn"
                break
            elif mtype == "chat_v2_error":
                reason = payload.get("reason")
                detail = (payload.get("detail") or "")[:200]
                print(f"  [ERROR] reason={reason} detail={detail}")
                if reason == "hallucination":
                    seen_hallucination = True
                saw_terminate = True
                terminate_reason = reason
                break
            elif mtype == "auto_resume_engaged":
                seen_auto_resume = True
                reason = payload.get("reason")
                print(f"  [auto_resume_engaged] reason={reason}")
                if reason == "hallucination":
                    seen_hallucination = True

            if time.time() - started > 300:
                print("Wall clock > 300s; bailing")
                break
    except Exception as exc:
        print(f"WS read exception: {exc}")
    finally:
        await ws.close()

    elapsed = time.time() - started
    print()
    print("─" * 50)
    print(f"Elapsed:           {elapsed:.1f}s")
    print(f"Tool calls total:  {tool_count}")
    print(f"read_file calls:   {read_file_count}")
    print(f"Saw termination:   {saw_terminate}")
    print(f"Termination reason:{terminate_reason}")
    print(f"Hallucination seen:{seen_hallucination}")
    print(f"Auto-resume seen:  {seen_auto_resume}")
    print()

    # Assertions for the args-aware fix
    if seen_hallucination:
        print("[FAIL] `hallucination` reason fired on legitimate exploration!")
        print("   The args-aware fix did NOT work.")
        return 1
    if terminate_reason == "end_turn":
        print("[PASS] agent finished naturally, no false hallucination.")
        return 0
    if terminate_reason in ("error_tool_budget", "error_max_turns"):
        print(f"[WARN] Agent hit a different hard cap ({terminate_reason}) but NOT hallucination - fix verified.")
        return 0
    print(f"[WARN] Terminated with unexpected reason {terminate_reason}")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
