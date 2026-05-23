"""MR-S2-13 真 LLM 测试：R-MISS-2 防覆盖。

forget 一条 fact → user 重提同事 → FactExtractor 应跳过插入
（is_forgotten_recently within 7 days）。
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
logging.getLogger("aiosqlite").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

from deskpet.memory.facts import FactsStore, FactExtractor
from deskpet.memory.memory_v2_schema import ensure_memory_v2_tables

DB = Path(r"G:\projects\deskpet\backend\userdata\data\state.db")


def dump(label):
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    print(f"\n=== {label} ===")
    rows = list(conn.execute(
        "SELECT id, key, value, is_active, forgotten_at FROM facts "
        "WHERE subject='user' ORDER BY id"
    ))
    if not rows:
        print("  (empty)")
    for r in rows:
        print(
            f"  id={r['id']} {r['key']!r}={r['value']!r} active={r['is_active']} "
            f"forgotten_at={r['forgotten_at']}"
        )
    conn.close()


async def make_llm():
    import httpx, keyring
    api_key = keyring.get_password("deskpet", "provider.chinzy")
    client = httpx.AsyncClient(
        base_url="https://chinzy.com/v1", timeout=120.0,
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
    return call, client


async def main():
    await ensure_memory_v2_tables(DB)
    store = FactsStore(DB)
    llm, client = await make_llm()
    try:
        ext = FactExtractor(store, extract_llm=llm, min_chars=4)

        # 清空老 fact
        conn = sqlite3.connect(str(DB))
        conn.execute("DELETE FROM facts")
        conn.commit()
        conn.close()

        # Step 1: write peanut allergy
        print("\n>>> Step 1: 我对花生过敏")
        r = await ext.process_message(
            message_id=2001, content="我对花生过敏",
            role="user", source="user_message",
        )
        print(f"    persisted: {r}")
        dump("AFTER initial write")

        # Step 2: forget it (by_id)
        if r:
            fid = r[0]["id"]
            op_id = uuid.uuid4().hex
            print(f"\n>>> Step 2: forget by_id fid={fid}")
            await store.mark_forgotten(fid, op_id=op_id, ts=time.time())
            dump("AFTER forget")

        # Step 3: user 重提相同事 — 应被跳过
        print("\n>>> Step 3: 我对花生过敏（重提，应被 R-MISS-2 跳过）")
        r2 = await ext.process_message(
            message_id=2002, content="我对花生过敏",
            role="user", source="user_message",
        )
        print(f"    persisted: {r2}")
        dump("AFTER re-extract (should NOT re-insert)")

        # Step 4: 模拟 8 天后（伪造 forgotten_at 为 -8d）
        print("\n>>> Step 4: 伪造 forgotten_at 为 8 天前，再 re-extract")
        conn = sqlite3.connect(str(DB))
        conn.execute(
            "UPDATE facts SET forgotten_at = ? WHERE forgotten_at IS NOT NULL",
            (time.time() - 8 * 86400,),
        )
        conn.commit()
        conn.close()
        r3 = await ext.process_message(
            message_id=2003, content="我对花生过敏",
            role="user", source="user_message",
        )
        print(f"    persisted: {r3}")
        dump("AFTER 8-day window expired (should re-insert)")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
