# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Stage 2 中文回测 fixture — F2 (memory-stage2-followup)。

在 Stage 1 的 35 条 message-recall QA 基础上，额外 seed 一批**只有
entity_path 才能命中**的 entity-targeted QA。

机制（对齐 MetricsRunner 真实契约）：
  * ``MetricsRunner`` 从 ``memory_qa_set`` **表**读 QA（不是从返回值），
    每条 QA 的 ``expected_msg_id`` 是召回应命中的 message_id。
  * 这里 seed 一批 facts，实体名（旺财 / 老李 / 上海 …）写在 ``value``
    里，``source_msg_id=None`` → 不挂任何消息 → 裸 Retriever 搜 messages
    表搜不到。
  * 再把 entity QA 写进 ``memory_qa_set`` 表，``expected_msg_id`` =
    ``_FACT_ID_OFFSET + fid``，正是 ``EnhancedRetriever._collect_entity_hits``
    命中 fact 时返回的 ``Hit.message_id``。

效果：
  * stage1（裸 Retriever）跑这些 entity QA → 0 命中（消息表无该实体）。
  * stage2（EnhancedRetriever + entity_path）→ RegexEntityExtractor 抽实体
    → ``find_by_entities`` LIKE value → 命中合成 fact id → hit。

于是 stage2 的 hit@5 **可量化地高于** stage1，``--strict`` 不再形同虚设。
全程 mock embedder + regex extractor，**确定性**，不依赖 BGE-M3 / LLM。
"""
from __future__ import annotations

import time
from typing import Any

import aiosqlite

from deskpet.memory.eval.zh_fixture import seed_zh_fixture
from deskpet.memory.enhanced_retriever import _FACT_ID_OFFSET
from deskpet.memory.facts import FactsStore


# (key, value_in_fact, query_mentioning_entity)
# entity 必须是 2-4 字中文 / 大写英文词（RegexEntityExtractor 能抽出），
# 且只出现在 fact value 里、不出现在任何 fixture 消息里。
_ENTITY_FACTS: list[tuple[str, str, str]] = [
    ("pet_name", "我家猫叫旺财，三岁橘猫", "旺财最近怎么样"),
    ("friend_li", "老李上周推荐了一本好书", "老李推荐了什么"),
    ("hometown", "我老家在上海浦东", "上海那边怎么样"),
    ("colleague_mike", "Mike is my teammate at work", "what did Mike say"),
    ("car", "我开的车是特斯拉", "特斯拉保养贵吗"),
    ("gym", "我常去铁馆健身房", "铁馆今天开门吗"),
    ("doctor", "我的牙医是王医生", "王医生几点上班"),
    ("game", "我最近在玩塞尔达", "塞尔达通关了吗"),
    ("band", "我喜欢周杰伦的歌", "周杰伦出新专辑了吗"),
    ("dish", "我爱吃毛血旺这道菜", "毛血旺辣不辣"),
]


async def seed_zh_fixture_stage2(db_path) -> dict[str, Any]:
    """Seed Stage 1 fixture + entity facts + entity QA（写进 memory_qa_set 表）。

    返回合并后的 seed_info（含 ``n_entity_facts`` / ``n_entity_qa``）。
    """
    base = await seed_zh_fixture(db_path)

    # mock 环境无 embedder → upsert 不写向量 → entity 路走 LIKE(value)。
    # 注意：FactsStore.upsert 内部自开 aiosqlite 连接；必须**先全部 upsert
    # 完成**再开连接写 QA —— 否则持有 conn 时调 upsert 会 database is locked。
    store = FactsStore(db_path)
    now = time.time()
    qa_rows: list[tuple[str, int]] = []  # (query, expected_msg_id)
    for key, value, query in _ENTITY_FACTS:
        fid = await store.upsert(
            category="profile",
            subject="user",
            key=key,
            value=value,
            confidence=0.95,
            source_msg_id=None,   # ★ 故意不挂消息：裸 Retriever 无从命中
            evidence=value,
        )
        qa_rows.append((query, _FACT_ID_OFFSET + fid))

    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA busy_timeout=5000")
        for query, expected in qa_rows:
            await conn.execute(
                "INSERT INTO memory_qa_set("
                "source, query, expected_msg_id, tags, created_at"
                ") VALUES (?, ?, ?, ?, ?)",
                ("zh_fixture_stage2", query, expected, "entity", now),
            )
        await conn.commit()
    n_facts = len(qa_rows)
    n_qa = len(qa_rows)

    out = dict(base)
    out["n_entity_facts"] = n_facts
    out["n_entity_qa"] = n_qa
    out["stage"] = "stage2"
    return out
