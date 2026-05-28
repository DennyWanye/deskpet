# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""MR-S2-4 真 LLM：episodic→semantic 固化通路端到端。

构造 1 个老 session（>20 message + 全部 31d 前）→ summarize_old_sessions
→ summary message 落表 → 异步 fact_extractor 拉 summary 文本 → facts
表新增 ≥ 1 条 category='episodic_summary'。
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import time
import uuid
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(r"G:\projects\deskpet\backend")))

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
for n in ("aiosqlite", "httpx", "httpcore", "urllib3"):
    logging.getLogger(n).setLevel(logging.WARNING)

import aiosqlite
from deskpet.memory.facts import FactsStore, FactExtractor
from deskpet.memory.memory_v2_schema import ensure_memory_v2_tables
from deskpet.memory.session_db import SessionDB
from deskpet.memory.summarizer import summarize_old_sessions

DB = Path(r"G:\projects\deskpet\backend\userdata\data\state.db")


async def make_llm_str():
    import httpx, keyring
    api_key = keyring.get_password("deskpet", "provider.the relay")
    client = httpx.AsyncClient(
        base_url="https://your-llm-relay.example.com/v1", timeout=120.0,
        headers={"Authorization": f"Bearer {api_key}"},
    )

    async def call(prompt):
        for _ in range(3):
            try:
                r = await client.post("/chat/completions", json={
                    "model": "deepseek-v4-pro",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                })
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"]
            except httpx.ReadError:
                await asyncio.sleep(2)
        raise RuntimeError("LLM ReadError x3")

    async def call_messages(messages):
        # summarize_old_sessions 用的是 messages -> {"content": ...} 形态
        for _ in range(3):
            try:
                r = await client.post("/chat/completions", json={
                    "model": "deepseek-v4-pro",
                    "messages": messages,
                    "temperature": 0.0,
                })
                r.raise_for_status()
                content = r.json()["choices"][0]["message"]["content"]
                return {"content": content}
            except httpx.ReadError:
                await asyncio.sleep(2)
        raise RuntimeError("LLM ReadError x3")

    return call, call_messages, client


def dump(label):
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    print(f"\n=== {label} ===")
    rows = list(conn.execute(
        "SELECT id, category, key, value, source_msg_id "
        "FROM facts WHERE category='episodic_summary' ORDER BY id"
    ))
    print(f"  episodic_summary facts: {len(rows)}")
    for r in rows:
        print(
            f"  id={r['id']} {r['key']!r}={r['value']!r:.60s} "
            f"source_msg_id={r['source_msg_id']}"
        )
    msg_count = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE is_summary=1"
    ).fetchone()[0]
    print(f"  summary messages: {msg_count}")
    conn.close()


async def seed_old_session(sid: str, n: int = 22) -> None:
    """注：用 35 天前的 created_at 让 cutoff 命中。"""
    sdb = SessionDB(db_path=DB)
    await sdb.initialize()
    cutoff = time.time() - 35 * 86400
    async with aiosqlite.connect(DB) as conn:
        for i in range(n):
            await conn.execute(
                "INSERT INTO messages (session_id, role, content, created_at, "
                "salience, decay_last_touch, is_summary) "
                "VALUES (?, ?, ?, ?, 0.5, ?, 0)",
                (
                    sid,
                    "user" if i % 2 == 0 else "assistant",
                    f"消息#{i}：我最喜欢的季节是冬天，喜欢喝热可可。"
                    if i % 4 == 0 else f"消息#{i}：聊聊周末做什么。",
                    cutoff - i * 10,
                    cutoff - i * 10,
                ),
            )
        await conn.commit()


async def main():
    await ensure_memory_v2_tables(DB)
    # 清旧 episodic_summary
    conn = sqlite3.connect(str(DB))
    conn.execute("DELETE FROM facts WHERE category='episodic_summary'")
    conn.execute("DELETE FROM messages WHERE is_summary=1")
    conn.commit()
    conn.close()

    sid = f"epi-{uuid.uuid4().hex[:8]}"
    await seed_old_session(sid, n=22)
    print(f"seeded session_id={sid} (22 messages, 35d ago)")

    str_llm, msg_llm, client = await make_llm_str()
    try:
        store = FactsStore(DB)
        ext = FactExtractor(store, extract_llm=str_llm, min_chars=4)

        bg: set[asyncio.Task] = set()
        result = await summarize_old_sessions(
            DB,
            msg_llm,
            age_days=30,
            min_messages=20,
            max_per_run=5,
            fact_extractor=ext,
            episodic_to_semantic=True,
            background_tasks=bg,
        )
        print(f"\nsummarize_old_sessions: scanned={result.sessions_scanned} "
              f"summarized={result.sessions_summarized} "
              f"archived={result.messages_archived} bg_tasks={len(bg)}")

        # 等异步 fact_extractor 完成
        if bg:
            print(f"等待 {len(bg)} 个后台 fact_extract task ...")
            await asyncio.gather(*list(bg), return_exceptions=True)
            print("done")
        dump("AFTER episodic_to_semantic")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
