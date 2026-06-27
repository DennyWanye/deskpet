# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from deskpet.memory.facts import FactExtractor, FactsStore
from deskpet.memory.migrator import ensure_v9


def _fact_json(key: str = "name", value: str = "Alice") -> str:
    return json.dumps(
        [
            {
                "category": "profile",
                "subject": "user",
                "key": key,
                "value": value,
                "confidence": 0.9,
                "evidence": "x",
            }
        ],
    )


@pytest_asyncio.fixture
async def db_path(tmp_path: Path) -> Path:
    db = tmp_path / "state.db"
    await ensure_v9(db)
    return db


def _extractor(
    db_path: Path,
    extract_llm,
    **kwargs,
) -> FactExtractor:
    return FactExtractor(
        FactsStore(db_path),
        extract_llm=extract_llm,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_content_dedup_skips_same_user_message_content(db_path: Path) -> None:
    llm = AsyncMock(return_value=_fact_json())
    extractor = _extractor(db_path, llm, content_dedup=True)

    first = await extractor.process_message(
        message_id=1,
        content="My name is Alice, please remember this stable profile.",
        role="user",
        source="user_message",
    )
    second = await extractor.process_message(
        message_id=2,
        content="My name is Alice, please remember this stable profile.",
        role="user",
        source="user_message",
    )

    assert len(first) == 1
    assert second == []
    assert llm.await_count == 1


@pytest.mark.asyncio
async def test_content_dedup_allows_different_user_message_content(
    db_path: Path,
) -> None:
    llm = AsyncMock(
        side_effect=[
            _fact_json("name", "Alice"),
            _fact_json("city", "Beijing"),
        ]
    )
    extractor = _extractor(db_path, llm, content_dedup=True)

    await extractor.process_message(
        message_id=1,
        content="My name is Alice, please remember this stable profile.",
        role="user",
        source="user_message",
    )
    await extractor.process_message(
        message_id=2,
        content="I have lived in Beijing for years, remember this profile.",
        role="user",
        source="user_message",
    )

    assert llm.await_count == 2


@pytest.mark.asyncio
async def test_content_dedup_ttl_expiry_allows_same_content_again(
    db_path: Path,
) -> None:
    llm = AsyncMock(
        side_effect=[
            _fact_json("name", "Alice"),
            _fact_json("name", "Alice"),
        ]
    )
    extractor = _extractor(
        db_path,
        llm,
        content_dedup=True,
        content_ttl_s=1,
    )

    await extractor.process_message(
        message_id=1,
        content="My name is Alice, please remember this stable profile.",
        role="user",
        source="user_message",
    )
    assert llm.await_count == 1

    for content_hash in list(extractor._recent_content):
        extractor._recent_content[content_hash] = time.time() - 2

    await extractor.process_message(
        message_id=2,
        content="My name is Alice, please remember this stable profile.",
        role="user",
        source="user_message",
    )

    assert llm.await_count == 2


@pytest.mark.asyncio
async def test_content_dedup_flag_off_does_not_skip_same_content(
    db_path: Path,
) -> None:
    llm = AsyncMock(
        side_effect=[
            _fact_json("name", "Alice"),
            _fact_json("city", "Beijing"),
        ]
    )
    extractor = _extractor(db_path, llm, content_dedup=False)

    await extractor.process_message(
        message_id=1,
        content="My name is Alice, please remember this stable profile.",
        role="user",
        source="user_message",
    )
    await extractor.process_message(
        message_id=2,
        content="My name is Alice, please remember this stable profile.",
        role="user",
        source="user_message",
    )

    assert llm.await_count == 2


@pytest.mark.asyncio
async def test_content_dedup_cache_max_evicts_oldest_entries(
    db_path: Path,
) -> None:
    llm = AsyncMock(return_value=_fact_json())
    extractor = _extractor(
        db_path,
        llm,
        content_dedup=True,
        content_cache_max=2,
    )

    for message_id, content in enumerate(
        [
            "First distinct stable profile content is long enough.",
            "Second distinct stable profile content is long enough.",
            "Third distinct stable profile content is long enough.",
        ],
        start=1,
    ):
        await extractor.process_message(
            message_id=message_id,
            content=content,
            role="user",
            source="user_message",
        )

    assert llm.await_count == 3
    assert len(extractor._recent_content) == 2
    # 证明淘汰的是 oldest（评估补 #4）：重发第 1 条（最早、已被淘汰）→ 应重新抽取
    await extractor.process_message(
        message_id=4,
        content="First distinct stable profile content is long enough.",
        role="user",
        source="user_message",
    )
    assert llm.await_count == 4, "最早 entry 应已被 LRU 淘汰 → 重发触发重抽"
    # 而第 3 条（最近）仍在缓存 → 重发应被短路、不重抽
    await extractor.process_message(
        message_id=5,
        content="Third distinct stable profile content is long enough.",
        role="user",
        source="user_message",
    )
    assert llm.await_count == 4, "最近 entry 仍在缓存 → 重发被短路"


@pytest.mark.asyncio
async def test_content_dedup_concurrent_same_content_calls_extract_once(
    db_path: Path,
) -> None:
    class SlowCountingLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self, prompt: str) -> str:
            self.calls += 1
            await asyncio.sleep(0.01)
            return _fact_json("name", "Alice")

    llm = SlowCountingLLM()
    extractor = _extractor(db_path, llm, content_dedup=True)

    results = await asyncio.gather(
        extractor.process_message(
            message_id=1,
            content="My name is Alice, please remember this stable profile.",
            role="user",
            source="user_message",
        ),
        extractor.process_message(
            message_id=2,
            content="My name is Alice, please remember this stable profile.",
            role="user",
            source="user_message",
        ),
    )

    assert llm.calls == 1
    assert sorted(len(result) for result in results) == [0, 1]


@pytest.mark.asyncio
async def test_content_dedup_placeholder_registered_before_llm_call(
    db_path: Path,
) -> None:
    """并发安全的**真实保护属性**（评估补 #3）：占位登记发生在 LLM 调用**之前**。

    这才是防并发 TOCTOU 双抽的根：第一条把占位写进 cache 后才去（慢）LLM 抽取，
    所以并发到达的第二条在 LLM 还没返回时就能命中占位被短路。
    note：plan 原设想的"去锁→==2"负向 sanity 在本实现下**无法复现**——判定+登记块内
    全是同步 dict 操作、无 await，asyncio 协作调度下该临界区本就原子（去锁也是 ==1），
    锁是面向"将来若在临界区引入 await"的防御。故改为直接证"登记先于 LLM"这一真实属性。
    """
    seen_in_cache: list[bool] = []

    extractor: FactExtractor = None  # type: ignore[assignment]

    async def _llm(prompt: str) -> str:
        # 被调用时，占位应已在 cache 里（证明登记早于 LLM）
        h = next(iter(extractor._recent_content), None)
        seen_in_cache.append(h is not None and len(extractor._recent_content) >= 1)
        return _fact_json("name", "Alice")

    extractor = _extractor(db_path, _llm, content_dedup=True)
    await extractor.process_message(
        message_id=1,
        content="My name is Alice, please remember this stable profile.",
        role="user",
        source="user_message",
    )
    assert seen_in_cache == [True], "LLM 被调用时占位必须已登记（登记先于 LLM）"


@pytest.mark.asyncio
async def test_content_dedup_llm_exception_revokes_placeholder(
    db_path: Path,
) -> None:
    llm = AsyncMock(side_effect=[RuntimeError("llm down"), _fact_json()])
    extractor = _extractor(db_path, llm, content_dedup=True)

    first = await extractor.process_message(
        message_id=1,
        content="My name is Alice, please remember this stable profile.",
        role="user",
        source="user_message",
    )
    second = await extractor.process_message(
        message_id=2,
        content="My name is Alice, please remember this stable profile.",
        role="user",
        source="user_message",
    )

    assert first == []
    assert len(second) == 1
    assert llm.await_count == 2


@pytest.mark.asyncio
async def test_content_dedup_bad_json_revokes_placeholder(db_path: Path) -> None:
    llm = AsyncMock(side_effect=["not a json array", _fact_json()])
    extractor = _extractor(db_path, llm, content_dedup=True)

    first = await extractor.process_message(
        message_id=1,
        content="My name is Alice, please remember this stable profile.",
        role="user",
        source="user_message",
    )
    second = await extractor.process_message(
        message_id=2,
        content="My name is Alice, please remember this stable profile.",
        role="user",
        source="user_message",
    )

    assert first == []
    assert len(second) == 1
    assert llm.await_count == 2


@pytest.mark.asyncio
async def test_content_dedup_does_not_skip_summarizer_source(
    db_path: Path,
) -> None:
    llm = AsyncMock(return_value=_fact_json())
    extractor = _extractor(db_path, llm, content_dedup=True)

    await extractor.process_message(
        message_id=1,
        content="summary text spanning many chars here",
        role="system",
        source="summarizer",
    )
    await extractor.process_message(
        message_id=2,
        content="summary text spanning many chars here",
        role="system",
        source="summarizer",
    )

    assert llm.await_count == 2


@pytest.mark.asyncio
async def test_clear_content_cache_allows_re_extract(db_path: Path) -> None:
    """T8（R5 自愈钩）：clear_content_cache() 后同内容会重新被抽取。"""
    llm = AsyncMock(return_value=_fact_json())
    extractor = _extractor(db_path, llm, content_dedup=True)
    text = "My name is Alice, please remember this stable profile."

    await extractor.process_message(message_id=1, content=text, role="user", source="user_message")
    # 第二次同内容 → 被短路
    await extractor.process_message(message_id=2, content=text, role="user", source="user_message")
    assert llm.call_count == 1

    # 清缓存（模拟 forget 后自愈）→ 第三次同内容应重抽
    await extractor.clear_content_cache()
    await extractor.process_message(message_id=3, content=text, role="user", source="user_message")
    assert llm.call_count == 2, "clear_content_cache 后同内容应可重新抽取"


def test_try_parse_facts_parse_ok_semantics() -> None:
    """评估 #1：parse_ok 语义——真畸形 → False（应撤销占位重试）；
    合法数组(含空) → True；prose 包裹的数组**有意救出** → True（容错，不误撤销）。"""
    from deskpet.memory.facts import _try_parse_facts

    # 真畸形（无数组/非 JSON）→ parse_ok=False
    assert _try_parse_facts("not a json array")[1] is False
    assert _try_parse_facts("")[1] is False
    assert _try_parse_facts("{plain text no brackets}")[1] is False
    # 合法空数组（LLM 说无可抽）→ parse_ok=True，facts 空
    facts, ok = _try_parse_facts("[]")
    assert ok is True and facts == []
    # 合法非空数组 → parse_ok=True，抽出 fact
    facts, ok = _try_parse_facts('[{"category":"profile","key":"name","value":"Alice"}]')
    assert ok is True and len(facts) == 1
    # prose 包裹（前后有文字）→ 有意救出内层数组（容错，非 bug）→ parse_ok=True
    facts, ok = _try_parse_facts('Here are facts: [{"category":"x","key":"k","value":"v"}] done')
    assert ok is True and len(facts) == 1


@pytest.mark.asyncio
async def test_empty_array_keeps_and_refreshes_placeholder(db_path: Path) -> None:
    """评估2 #缺口：LLM 返回合法空数组 `[]`（parse_ok=True, 0 条）→ 占位保留，
    且时间戳刷新为"完成时刻"（MAJOR-1），不是 LLM 调用前的抢占登记时刻。"""
    calls = {"n": 0}

    async def _llm(prompt: str) -> str:
        calls["n"] += 1
        await asyncio.sleep(0.03)   # 制造"登记时刻"与"完成时刻"的可测时间差
        return "[]"                 # 合法空数组

    extractor = _extractor(db_path, _llm, content_dedup=True)
    text = "Some long enough content that yields no extractable facts here."

    t_before = time.time()
    res = await extractor.process_message(
        message_id=1, content=text, role="user", source="user_message"
    )
    assert res == [] and calls["n"] == 1
    # 占位保留
    assert len(extractor._recent_content) == 1
    h = next(iter(extractor._recent_content))
    # 时间戳刷新为完成时刻（>= LLM 耗时 0.03s 之后），证明不是停在抢占登记的 t_before
    assert extractor._recent_content[h] >= t_before + 0.03 - 0.005

    # 立即重发同内容 → 仍被短路（占位新鲜），LLM 不再被调
    res2 = await extractor.process_message(
        message_id=2, content=text, role="user", source="user_message"
    )
    assert res2 == [] and calls["n"] == 1
