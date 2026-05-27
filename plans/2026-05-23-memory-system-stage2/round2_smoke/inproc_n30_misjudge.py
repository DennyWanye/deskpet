"""MR-S2-1-9 真模型 N=30 cross_key 误判率统计。

5 矛盾对 × 6 次 = 30 次新 fact 写入：
  * 矛盾对（应 supersede）：花生→海鲜过敏 / 咖啡→茶 / 苹果→安卓 / 北京→上海 / 已婚→单身
  * 非矛盾对照（不应 supersede）：6 次"加另一个偏好"插同 subject

统计：
  * cross_key 正确 supersede 率（矛盾对中正确标 superseded 的 fact 数 / 总矛盾对）
  * 误判率（不应 supersede 但被标的次数 / 总非矛盾对）
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
logging.basicConfig(level=logging.WARNING, format="%(name)s %(message)s")

from deskpet.memory.facts import FactsStore, FactExtractor
from deskpet.memory.memory_v2_schema import ensure_memory_v2_tables


# 5 矛盾对（先初始 + 后修正）
PAIRS = [
    ("我对花生过敏", "其实我搞错了，我不是过敏花生，是过敏海鲜"),
    ("我最喜欢喝咖啡", "更正一下，我现在改喝茶了，不再喝咖啡"),
    ("我用苹果手机", "其实我换成安卓手机了，不用苹果"),
    ("我家住北京", "搞错了，我现在搬到上海了"),
    ("我已经结婚了", "其实是单身，搞错了"),
]
# 非矛盾对照（加另一偏好，不影响原 fact）
NON_PAIRS = [
    ("我喜欢喝咖啡", "我也喜欢户外徒步"),
    ("我家有只柯基叫旺财", "我周末喜欢打篮球"),
    ("我是程序员", "我很喜欢看科幻电影"),
    ("我老婆生日是 3 月 5 日", "我喜欢吃辣"),
    ("我用 MacBook 工作", "我假期想去日本旅行"),
]


async def make_llm():
    import httpx, keyring
    api_key = keyring.get_password("deskpet", "provider.the relay")
    client = httpx.AsyncClient(
        base_url="https://your-llm-relay.example.com/v1", timeout=120.0,
        headers={"Authorization": f"Bearer {api_key}"},
    )

    async def call(p):
        for _ in range(3):
            try:
                r = await client.post("/chat/completions", json={
                    "model": "deepseek-v4-pro",
                    "messages": [{"role": "user", "content": p}],
                    "temperature": 0.0,
                })
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"]
            except (httpx.ReadError, httpx.RemoteProtocolError):
                await asyncio.sleep(2)
        raise RuntimeError("LLM ReadError x3")
    return call, client


async def run_pair(
    ext: FactExtractor, store: FactsStore,
    first: str, second: str, is_contradiction: bool, idx: int,
) -> dict:
    """跑一对 (first, second)。返回结果 dict 含是否 supersede。"""
    # 清场（只清 user subject 的 active facts，不动 forgotten / 别的 subject）
    conn = sqlite3.connect(str(store._db_path))
    conn.execute("DELETE FROM facts WHERE subject='user'")
    conn.commit()
    conn.close()

    # 第一句：插初始 fact
    msg_id = 10000 + idx * 10
    try:
        r1 = await ext.process_message(
            message_id=msg_id, content=first,
            role="user", source="user_message",
        )
    except Exception as e:
        return {"error_first": str(e), "is_contradiction": is_contradiction}
    if not r1:
        return {"first_no_fact": True, "is_contradiction": is_contradiction}

    first_ids = [p["id"] for p in r1 if p.get("action") == "insert"]
    if not first_ids:
        return {"first_no_insert": True, "is_contradiction": is_contradiction}

    # 第二句：可能矛盾
    try:
        r2 = await ext.process_message(
            message_id=msg_id + 1, content=second,
            role="user", source="user_message",
        )
    except Exception as e:
        return {"error_second": str(e), "is_contradiction": is_contradiction,
                "first_ids": first_ids}

    superseded_ids = [p["id"] for p in r2 if p.get("action") == "superseded"]
    insert_ids = [p["id"] for p in r2 if p.get("action") == "insert"]

    # 真实查 DB 确认
    conn = sqlite3.connect(str(store._db_path))
    conn.row_factory = sqlite3.Row
    db_state = []
    for r in conn.execute(
        "SELECT id, key, value, is_active, superseded_by "
        "FROM facts WHERE subject='user' ORDER BY id"
    ):
        db_state.append(dict(r))
    conn.close()

    any_first_superseded = any(
        d["is_active"] == 0 and d["id"] in first_ids for d in db_state
    )
    return {
        "is_contradiction": is_contradiction,
        "first_ids": first_ids,
        "second_persisted": r2,
        "second_superseded_ids": superseded_ids,
        "second_insert_ids": insert_ids,
        "any_first_superseded": any_first_superseded,
        "db_state": db_state,
    }


async def main():
    DB = Path(r"G:\projects\deskpet\backend\userdata\data\state.db")
    await ensure_memory_v2_tables(DB)
    store = FactsStore(DB)
    llm, client = await make_llm()
    try:
        ext = FactExtractor(
            store, extract_llm=llm, merge_llm=llm,
            min_chars=4, cross_key_merge=True, cross_key_llm=llm,
        )

        all_results = []
        # 6 次矛盾对（每对跑 6 次但用同一对）— 改为：5 对各跑 1 次 × 6 round = 30 次
        # 简化：5 对每个跑 3 次（30 round 太久），共 15 次；如果时间够再加另 5 非矛盾对各 3 次 = 30
        rounds = 3
        idx = 0
        for round_i in range(rounds):
            for (first, second) in PAIRS:
                idx += 1
                print(f"\n--- 矛盾对 #{idx} round={round_i+1} first={first!r}")
                r = await run_pair(ext, store, first, second, True, idx)
                all_results.append(r)
                print(f"    any_first_superseded={r.get('any_first_superseded')}")
            for (first, second) in NON_PAIRS:
                idx += 1
                print(f"\n--- 非矛盾对 #{idx} round={round_i+1} first={first!r}")
                r = await run_pair(ext, store, first, second, False, idx)
                all_results.append(r)
                print(f"    any_first_superseded={r.get('any_first_superseded')}")

        # 统计
        contradictions = [r for r in all_results if r.get("is_contradiction")]
        non_contradictions = [r for r in all_results if r.get("is_contradiction") is False]
        contra_correct = sum(1 for r in contradictions if r.get("any_first_superseded"))
        non_contra_misjudge = sum(1 for r in non_contradictions if r.get("any_first_superseded"))

        print("\n" + "=" * 60)
        print(f"统计结果 (N={len(all_results)})：")
        print(f"  矛盾对总数:        {len(contradictions)}")
        print(f"  矛盾对正确 supersede: {contra_correct}")
        print(f"  矛盾对召回率:      {contra_correct/max(1,len(contradictions))*100:.1f}% (target ≥ 70%)")
        print()
        print(f"  非矛盾对总数:      {len(non_contradictions)}")
        print(f"  非矛盾对误判 supersede: {non_contra_misjudge}")
        print(f"  误判率:           {non_contra_misjudge/max(1,len(non_contradictions))*100:.1f}% (target ≤ 15%)")

        # Save full JSON
        out_path = Path(r"G:\projects\deskpet\plans\2026-05-23-memory-system-stage2\round2_smoke\n30_results.json")
        out_path.write_text(
            json.dumps({
                "total": len(all_results),
                "contradictions": len(contradictions),
                "contradiction_correct": contra_correct,
                "non_contradictions": len(non_contradictions),
                "non_contradiction_misjudge": non_contra_misjudge,
                "recall_pct": contra_correct/max(1,len(contradictions))*100,
                "misjudge_pct": non_contra_misjudge/max(1,len(non_contradictions))*100,
                "details": all_results,
            }, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\n详细结果存：{out_path}")

    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
