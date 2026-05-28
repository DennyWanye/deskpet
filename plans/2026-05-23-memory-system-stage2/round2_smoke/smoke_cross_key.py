# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""MR-S2-1 真人工测试：cross-key 矛盾治理（真 LLM via the relay deepseek-v4-pro）。

跑前提：
  * backend 已起在 8100 + Stage 2 全 flag 已开
  * userdata config 已写 [memory.v2] cross_key_merge=true

步骤：
  1. user "我对花生过敏" → 预期 facts 表插 fact A (allergy_peanut)
  2. user 隔几轮无关对话
  3. user "其实我搞错了，我不过敏花生，过敏海鲜" → 预期 cross_key
     LLM 判矛盾 → fact A is_active=0 + superseded_by=<新 fact B id>
  4. sqlite3 dump facts 表
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import time
import uuid
from pathlib import Path

import websockets

# UTF-8 stdout（Windows 默认 GBK 会让中文乱码）
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except AttributeError:
        pass

_SECRET = "8bde0016004283abde8540fa9fea7064"
_DB_PATH = Path(r"G:\projects\deskpet\backend\userdata\data\state.db")
_SID = f"mr_s2_1_{uuid.uuid4().hex[:8]}"


def _dump_facts(label: str) -> None:
    print(f"\n=== {label} ===")
    if not _DB_PATH.is_file():
        print(f"  (DB not found at {_DB_PATH})")
        return
    conn = sqlite3.connect(str(_DB_PATH))
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT id, category, subject, key, value, is_active, "
            "superseded_by, forgotten_at, "
            "datetime(updated_at, 'unixepoch','localtime') as updated "
            "FROM facts WHERE subject='user' "
            "ORDER BY id DESC LIMIT 20"
        )
        rows = cur.fetchall()
        if not rows:
            print("  (no facts for subject=user)")
            return
        for r in rows:
            print(
                f"  id={r['id']} {r['key']!r}={r['value']!r:.50} "
                f"active={r['is_active']} superseded_by={r['superseded_by']} "
                f"forgotten_at={r['forgotten_at']} updated={r['updated']}"
            )
    finally:
        conn.close()


async def _chat_and_wait(ws, content: str, *, timeout: float = 90) -> str:
    """Send chat + collect assistant_text + tool calls until done."""
    print(f"\n>>> USER: {content}")
    await ws.send(json.dumps({
        "type": "chat",
        "payload": {"text": content},
    }))
    full = []
    tool_calls = []
    deadline = time.time() + timeout
    last_active = time.time()
    while time.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
        except asyncio.TimeoutError:
            # 8 秒无消息且已有内容 → 视为完成
            if (time.time() - last_active) > 8 and full:
                break
            continue
        last_active = time.time()
        try:
            m = json.loads(raw)
        except Exception:
            continue
        t = m.get("type", "")
        p = m.get("payload", {}) or {}
        if t in ("chat_v2_final", "chat_final", "assistant_final"):
            full.append(p.get("text") or "")
            break
        if t in ("chat_v2_chunk", "assistant_text", "stream_chunk", "chat_v2_delta"):
            full.append(p.get("text") or p.get("delta") or "")
        elif t in ("assistant_done", "chat_done", "stream_done", "done", "chat_v2_done"):
            break
        elif t in ("tool_invoke", "tool_call"):
            tool_calls.append(p)
    reply = "".join(full)
    print(f"<<< ASSISTANT: {reply[:300]}{'...' if len(reply) > 300 else ''}")
    if tool_calls:
        for tc in tool_calls[:3]:
            print(f"    [tool {tc.get('name','?')} args={str(tc.get('args',{}))[:120]}]")
    return reply


async def main() -> None:
    url = f"ws://127.0.0.1:8100/ws/control?secret={_SECRET}&session_id={_SID}"
    print(f"Connecting to {url}")
    _dump_facts("BEFORE")

    async with websockets.connect(url, max_size=10**7) as ws:
        # 1. peanut allergy
        await _chat_and_wait(ws, "你好，我对花生过敏，记一下哦。")
        await asyncio.sleep(8)  # 等异步 fact 抽取
        _dump_facts("AFTER peanut")

        # 2. 隔几轮无关对话
        for filler in ("今天天气怎么样？", "随便聊聊", "讲个笑话吧"):
            await _chat_and_wait(ws, filler)
            await asyncio.sleep(2)

        # 3. seafood contradiction
        await _chat_and_wait(
            ws,
            "等等，我搞错了，其实我不过敏花生，是过敏海鲜，麻烦更新一下。",
        )
        await asyncio.sleep(12)  # 等 cross_key LLM + 持久化
        _dump_facts("AFTER seafood (cross_key)")


if __name__ == "__main__":
    if "--dump-only" in sys.argv:
        _dump_facts("DUMP-ONLY")
        sys.exit(0)
    asyncio.run(main())
