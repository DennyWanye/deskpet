# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""MR-S2-3 真 LLM：entity 索引检索路。"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(r"G:\projects\deskpet\backend")))

from deskpet.memory.facts import FactsStore
from deskpet.memory.entity_extractor import (
    LLMEntityExtractor, RegexEntityExtractor, CompositeEntityExtractor,
)
from deskpet.memory.memory_v2_schema import ensure_memory_v2_tables

DB = Path(r"G:\projects\deskpet\backend\userdata\data\state.db")


async def make_llm():
    import httpx, keyring
    api_key = keyring.get_password("deskpet", "provider.the relay")
    client = httpx.AsyncClient(
        base_url="https://your-llm-relay.example.com/v1", timeout=60.0,
        headers={"Authorization": f"Bearer {api_key}"},
    )

    async def call(p):
        r = await client.post("/chat/completions", json={
            "model": "deepseek-v4-pro",
            "messages": [{"role": "user", "content": p}],
            "temperature": 0.0,
        })
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    return call, client


async def main():
    await ensure_memory_v2_tables(DB)
    store = FactsStore(DB)

    # 清，注入两条带实体 fact
    conn = sqlite3.connect(str(DB))
    conn.execute("DELETE FROM facts WHERE category='profile' AND key IN ('pet_name','friend')")
    conn.commit()
    conn.close()
    await store.upsert(
        category="profile", subject="user", key="pet_name",
        value="家里养了只柯基叫旺财", confidence=0.95,
        source_msg_id=3001, evidence="家里养了只柯基叫旺财",
    )
    await store.upsert(
        category="profile", subject="user", key="friend",
        value="老李上周给我推荐了一本书《XXX》", confidence=0.95,
        source_msg_id=3002, evidence="老李上周给我推荐了一本书《XXX》",
    )

    llm, client = await make_llm()
    try:
        composite = CompositeEntityExtractor(
            LLMEntityExtractor(llm),
            RegexEntityExtractor(),
        )
        for query in (
            "旺财怎么样了",
            "老李上次说什么",
            "what about Mike",
            "今天怎么了",   # 应被停用词过滤
        ):
            print(f"\n>>> query={query!r}")
            ents = await composite.extract(query)
            print(f"  entities: {ents}")
            hits = await store.find_by_entities(ents, limit=10)
            print(f"  find_by_entities hits: {len(hits)}")
            for h in hits[:3]:
                print(f"    fid={h['id']} {h['key']}={h['value']}")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
