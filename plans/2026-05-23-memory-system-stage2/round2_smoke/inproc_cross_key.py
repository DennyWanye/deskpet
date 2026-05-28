# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""MR-S2-1 in-process 真 LLM cross-key 矛盾治理验证。

绕开 chat_v2 链路（the relay 抖动严重）—— 直接构造 FactExtractor +
真 LLM (the relay deepseek-v4-pro) 跑 process_message。
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(r"G:\projects\deskpet\backend")))

import logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(name)s] %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

from deskpet.memory.facts import FactsStore, FactExtractor
from deskpet.memory.memory_v2_schema import ensure_memory_v2_tables


DB = Path(r"G:\projects\deskpet\backend\userdata\data\state.db")


async def make_llm():
    """直接 httpx 拼 chat completions (the relay)。"""
    import httpx
    import keyring

    api_key = (
        keyring.get_password("deskpet", "provider.relay")
        or keyring.get_password("deskpet", "provider.relay-deepseek")
    )
    if not api_key:
        print("ERROR: no the relay api key in keyring", file=sys.stderr)
        sys.exit(2)
    print(f"  api_key 命中, 长度={len(api_key)}", file=sys.stderr)

    client = httpx.AsyncClient(
        base_url="https://your-llm-relay.example.com/v1",
        timeout=120.0,
        headers={"Authorization": f"Bearer {api_key}"},
    )

    call_counter = {"n": 0}

    async def call(prompt: str) -> str:
        call_counter["n"] += 1
        cid = call_counter["n"]
        is_cross = "EXISTING active facts on the same subject" in prompt
        is_extract = "extracting stable facts" in prompt
        kind = "CROSS_KEY" if is_cross else ("EXTRACT" if is_extract else "OTHER")
        print(f"\n  [LLM #{cid} {kind}] prompt last 500 chars:")
        print(f"    {prompt[-500:]!r}")
        retries = 0
        while retries < 3:
            try:
                resp = await client.post(
                    "/chat/completions",
                    json={
                        "model": "deepseek-v4-pro",
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.0,
                    },
                )
                resp.raise_for_status()
                out = resp.json()["choices"][0]["message"]["content"]
                print(f"  [LLM #{cid} {kind}] response:\n    {out!r}")
                return out
            except httpx.ReadError:
                retries += 1
                print(f"  [retry {retries}/3 ReadError]", file=sys.stderr)
                await asyncio.sleep(2)
        raise RuntimeError("LLM failed after 3 retries")

    return call, client


def dump(label: str) -> None:
    print(f"\n=== {label} ===")
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    try:
        for r in conn.execute(
            "SELECT id, category, key, value, is_active, superseded_by, "
            "forgotten_at FROM facts WHERE subject='user' ORDER BY id"
        ):
            print(
                f"  id={r['id']} {r['category']} {r['key']!r}={r['value']!r} "
                f"active={r['is_active']} superseded_by={r['superseded_by']} "
                f"forgotten_at={r['forgotten_at']}"
            )
        if not conn.execute("SELECT COUNT(*) FROM facts WHERE subject='user'").fetchone()[0]:
            print("  (empty)")
    finally:
        conn.close()


async def main() -> None:
    await ensure_memory_v2_tables(DB)
    store = FactsStore(DB)
    llm, client = await make_llm()
    try:
        ext = FactExtractor(
            store,
            extract_llm=llm,
            merge_llm=llm,
            min_chars=4,  # 放宽方便测试
            cross_key_merge=True,
            cross_key_llm=llm,
        )

        dump("BEFORE")

        # Step 1: peanut allergy
        print("\n>>> process_message: 我对花生过敏")
        r = await ext.process_message(
            message_id=1001, content="我对花生过敏",
            role="user", source="user_message",
        )
        print(f"    persisted: {r}")
        dump("AFTER peanut")

        # Step 2: seafood contradiction
        print("\n>>> process_message: 等等，我搞错了，我不是过敏花生，是过敏海鲜")
        r = await ext.process_message(
            message_id=1002,
            content="等等，我搞错了，我不是过敏花生，是过敏海鲜",
            role="user", source="user_message",
        )
        print(f"    persisted: {r}")
        dump("AFTER seafood (cross_key check)")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
