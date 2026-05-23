"""TG-2 — facts 抽取接入集成测试（WI-M1.2）。

集成层：真实 ``SessionDB`` + 真实 ``FactExtractor`` + 与 main.py 同形状的
``_on_message_written`` 组合 fanout callable。验证「wire 进 append_message
写入链后，facts 抽取在真实运行栈里确实/确实不发生」—— 单元测试不可替代。
"""
from __future__ import annotations

import asyncio
import json

import aiosqlite
import pytest

from deskpet.memory.session_db import SessionDB
from deskpet.memory.facts import FactsStore, FactExtractor
from deskpet.memory.memory_v2_schema import _reset_cache_for_tests


@pytest.fixture
def db_path(tmp_path):
    _reset_cache_for_tests()
    return tmp_path / "state.db"


# --- mock LLM：按 prompt 形状分流 extract / merge ----------------------
def _make_mock_llm(extract_fact: dict | None, merge_action: str = "replace"):
    """返回一个 (prompt:str)->str 的 mock LLM。

    extract prompt（含 'SOURCE:'）→ 返回含 extract_fact 的 JSON 数组；
    merge prompt（含 'NEW fact:'）→ 返回 {action, value}。
    """
    async def _llm(prompt: str) -> str:
        if "NEW fact:" in prompt or "DECISION:" in prompt:
            return json.dumps({
                "action": merge_action,
                "value": "海鲜过敏" if merge_action != "no_op" else None,
                "reason": "test",
            })
        # extract
        if extract_fact is None:
            return "[]"
        return json.dumps([extract_fact])
    return _llm


_PEANUT_FACT = {
    "category": "preference", "subject": "user", "key": "food_allergy",
    "value": "花生过敏", "confidence": 0.9, "evidence": "我对花生过敏",
}


def _make_fanout(sdb: SessionDB, extractor: FactExtractor | None, flag: dict):
    """与 main.py 同形状的 2 参 fanout：按 flag 决定是否抽 facts。"""
    async def _on_msg(mid: int, text: str) -> None:
        if not flag.get("on") or extractor is None:
            return
        role = await sdb.get_message_role(mid)
        if role:
            await extractor.process_message(
                message_id=mid, content=text, role=role,
            )
    return _on_msg


async def _facts_rows(db_path) -> list[dict]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        try:
            cur = await db.execute("SELECT * FROM facts")
        except aiosqlite.OperationalError:
            return []  # facts 表不存在
        rows = await cur.fetchall()
        await cur.close()
    return [dict(r) for r in rows]


async def _table_exists(db_path, name: str) -> bool:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        )
        row = await cur.fetchone()
        await cur.close()
    return row is not None


# --- T2-1 ----------------------------------------------------------------
@pytest.mark.asyncio
async def test_t2_1_flag_on_user_message_extracts_fact(db_path):
    sdb = SessionDB(db_path=db_path)
    await sdb.initialize()
    store = FactsStore(db_path)
    extractor = FactExtractor(store, extract_llm=_make_mock_llm(_PEANUT_FACT))
    flag = {"on": True}
    sdb._on_message_written = _make_fanout(sdb, extractor, flag)

    await sdb.append_message(session_id="s1", role="user", content="我对花生过敏，吃了会喉咙肿")
    rows = await _facts_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["subject"] == "user"
    assert "花生" in rows[0]["value"]


# --- T2-2 ----------------------------------------------------------------
@pytest.mark.asyncio
async def test_t2_2_assistant_message_also_extracted(db_path):
    sdb = SessionDB(db_path=db_path)
    await sdb.initialize()
    store = FactsStore(db_path)
    extractor = FactExtractor(store, extract_llm=_make_mock_llm(_PEANUT_FACT))
    sdb._on_message_written = _make_fanout(sdb, extractor, {"on": True})

    await sdb.append_message(
        session_id="s1", role="assistant",
        content="我记住了，以后给你推荐零食会避开花生。",
    )
    assert len(await _facts_rows(db_path)) == 1


# --- T2-3 ----------------------------------------------------------------
@pytest.mark.asyncio
async def test_t2_3_tool_and_short_messages_skipped(db_path):
    sdb = SessionDB(db_path=db_path)
    await sdb.initialize()
    store = FactsStore(db_path)
    extractor = FactExtractor(
        store, extract_llm=_make_mock_llm(_PEANUT_FACT), min_chars=8,
    )
    sdb._on_message_written = _make_fanout(sdb, extractor, {"on": True})

    await sdb.append_message(session_id="s1", role="tool", content='{"x":1}')
    await sdb.append_message(session_id="s1", role="user", content="嗯")
    assert await _facts_rows(db_path) == []


# --- T2-4 ----------------------------------------------------------------
@pytest.mark.asyncio
async def test_t2_4_flag_off_zero_extraction(db_path):
    sdb = SessionDB(db_path=db_path)
    await sdb.initialize()
    calls = {"n": 0}

    class _CountingExtractor(FactExtractor):
        async def process_message(self, **kw):  # type: ignore[override]
            calls["n"] += 1
            return await super().process_message(**kw)

    extractor = _CountingExtractor(
        FactsStore(db_path), extract_llm=_make_mock_llm(_PEANUT_FACT),
    )
    sdb._on_message_written = _make_fanout(sdb, extractor, {"on": False})

    await sdb.append_message(session_id="s1", role="user", content="我对花生过敏，吃了会喉咙肿")
    assert calls["n"] == 0
    # Strangler-Fig：flag 关 → facts 表根本没被创建
    assert not await _table_exists(db_path, "facts")


# --- T2-5 ----------------------------------------------------------------
@pytest.mark.asyncio
async def test_t2_5_conflicting_fact_merges_not_duplicates(db_path):
    sdb = SessionDB(db_path=db_path)
    await sdb.initialize()
    store = FactsStore(db_path)
    extractor = FactExtractor(
        store,
        extract_llm=_make_mock_llm(_PEANUT_FACT, merge_action="replace"),
    )
    sdb._on_message_written = _make_fanout(sdb, extractor, {"on": True})

    await sdb.append_message(session_id="s1", role="user", content="我对花生过敏，吃了会喉咙肿")
    # 第二条相同 (subject,key)，merge LLM 决定 replace。
    await sdb.append_message(
        session_id="s1", role="user", content="其实我不过敏花生，是过敏海鲜",
    )
    active = [r for r in await _facts_rows(db_path) if r["is_active"] == 1]
    assert len(active) == 1, "冲突事实应 merge/replace，不能并存两条 active"


# --- T2-6 并发 -----------------------------------------------------------
@pytest.mark.asyncio
async def test_t2_6_concurrent_extraction_no_duplicate_active(db_path):
    sdb = SessionDB(db_path=db_path)
    await sdb.initialize()
    store = FactsStore(db_path)
    extractor = FactExtractor(
        store,
        extract_llm=_make_mock_llm(_PEANUT_FACT, merge_action="no_op"),
    )
    # 同 (subject,key) 的两条消息并发抽取 —— _persist_lock 应串行化
    # find→upsert，不出现两条 active。
    await asyncio.gather(
        extractor.process_message(message_id=1, content="我对花生过敏，吃了会喉咙肿", role="user"),
        extractor.process_message(message_id=2, content="我对花生过敏，平时得避开", role="user"),
    )
    active = [r for r in await _facts_rows(db_path) if r["is_active"] == 1]
    assert len(active) == 1


# --- T2-7 异步非阻塞 -----------------------------------------------------
@pytest.mark.asyncio
async def test_t2_7_append_message_not_blocked_by_extraction(db_path):
    sdb = SessionDB(db_path=db_path)
    await sdb.initialize()
    store = FactsStore(db_path)
    order: list[str] = []

    async def _slow_llm(prompt: str) -> str:
        await asyncio.sleep(0.3)
        order.append("llm_done")
        return json.dumps([_PEANUT_FACT])

    extractor = FactExtractor(store, extract_llm=_slow_llm)
    tasks: list[asyncio.Task] = []

    async def _on_msg(mid: int, text: str) -> None:
        role = await sdb.get_message_role(mid)
        tasks.append(asyncio.create_task(
            extractor.process_message(message_id=mid, content=text, role=role)
        ))

    sdb._on_message_written = _on_msg
    await sdb.append_message(session_id="s1", role="user", content="我对花生过敏，吃了会喉咙肿")
    order.append("append_returned")
    await asyncio.gather(*tasks)
    # append_message 在 LLM 返回前就 resolve。
    assert order[0] == "append_returned"


# --- T2-8 LLM 失败隔离 ---------------------------------------------------
@pytest.mark.asyncio
async def test_t2_8_llm_failure_does_not_break_append(db_path):
    sdb = SessionDB(db_path=db_path)
    await sdb.initialize()
    store = FactsStore(db_path)

    async def _broken_llm(prompt: str) -> str:
        raise RuntimeError("llm boom")

    extractor = FactExtractor(store, extract_llm=_broken_llm)
    sdb._on_message_written = _make_fanout(sdb, extractor, {"on": True})
    # append 不应抛 —— FactExtractor 内部失败隔离。
    mid = await sdb.append_message(
        session_id="s1", role="user", content="我对花生过敏",
    )
    assert mid > 0
