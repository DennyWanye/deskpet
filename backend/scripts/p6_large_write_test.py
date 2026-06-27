# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Force the agent to write a large React file via write_file —
reproduces the max_tokens-truncation bug + verifies the 8192 fix.

Pre-fix (max_tokens=2048): LLM cuts off mid-string → JSON parse fail.
Post-fix (max_tokens=8192): LLM has headroom to finish 5-6KB output.
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

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

SECRET = os.environ.get("DESKPET_SECRET", "")
if not SECRET:
    try:
        with open("/path/to/deskpet/tauri-dev.log", "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        m = list(re.finditer(r"secret=([a-f0-9]{32})", text))
        if m:
            SECRET = m[-1].group(1)
    except Exception:
        pass
print(f"Using secret={SECRET[:8]}... ({len(SECRET)} chars)")
WS = f"ws://127.0.0.1:8100/ws/control?secret={SECRET}&session_id=p6-largewrite-test"

PROMPT = (
    "请用 write_file 工具创建文件 G:/projects/小说网站/client/src/pages/TestLargeFile.jsx，"
    "写一个完整的 React 函数式组件 BookListPage，包含：useState 多个 state、useEffect "
    "数据获取、3 个子组件、SearchBar、CategoryFilter、PaginatedBookList，含 Tailwind CSS "
    "样式、错误处理、loading 状态、分页逻辑、点击跳转。请写完整代码大约 5000 字符。"
    "一次性 write_file 调用搞定，不要分多次。"
)


async def main() -> int:
    print(f"Connecting to {WS}")
    try:
        ws = await asyncio.wait_for(websockets.connect(WS), timeout=5.0)
    except Exception as exc:
        print(f"WS connect failed: {exc}")
        return 1
    print("Connected. Sending large-write prompt...")
    await ws.send(json.dumps({
        "type": "chat_v2",
        "payload": {"text": PROMPT, "session_id": "p6-largewrite-test"},
    }))
    started = time.time()
    tool_calls = 0
    saw_repaired = False
    saw_malformed = False
    saw_truncated_hint = False
    saw_final = False
    terminate_reason: str | None = None
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
                tool_calls += 1
                tc = payload.get("tool_call") or {}
                print(f"  [tool_call #{tool_calls}] {tc.get('name')}")
            elif mtype == "tool_result":
                # See if hint about truncation came back
                r = payload.get("result") or ""
                if isinstance(r, str):
                    if "max_tokens_truncation" in r or "被 LLM 输出 token 上限截断" in r:
                        saw_truncated_hint = True
                        print("  [TRUNCATION HINT DELIVERED]")
            elif mtype == "chat_v2_final":
                final_text = payload.get("text") or payload.get("content") or ""
                print(f"  [final] text_len={len(final_text)}")
                saw_final = True
                terminate_reason = "end_turn"
                break
            elif mtype == "chat_v2_error":
                reason = payload.get("reason")
                detail = (payload.get("detail") or "")[:200]
                print(f"  [ERROR] reason={reason} detail={detail}")
                terminate_reason = reason
                break
            if time.time() - started > 300:
                print("Timeout 300s; bailing")
                break
    except Exception as exc:
        print(f"WS read exception: {exc}")
    finally:
        await ws.close()
    elapsed = time.time() - started
    print()
    print("─" * 50)
    print(f"Elapsed: {elapsed:.1f}s")
    print(f"Tool calls: {tool_calls}")
    print(f"Saw final: {saw_final}")
    print(f"Termination: {terminate_reason}")
    print(f"Saw truncation hint: {saw_truncated_hint}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
