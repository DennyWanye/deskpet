"""P4-S1 tasks 3.1 + 3.3 + 3.4 — SessionDB 单元测试。

覆盖：
  * initialize 幂等 + WAL 模式实际生效
  * create_session / append_message / get_messages 基本 CRUD
  * FTS5 trigger 同步（append → search_fts 命中；delete → 不命中）
  * FTS5 中文 + 英文同时 MATCH
  * update_salience 更新 salience 列 + decay_last_touch 可选跳过
  * tool_calls JSON 序列化 + 反序列化 roundtrip
  * session_id 过滤在 search_fts 生效

perf 测试（10K insert + concurrent write retry）在
``test_deskpet_session_db_perf.py``，默认 ``-m "not perf"`` 跳过。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio

from deskpet.memory.session_db import SessionDB
from memory.base import ConversationTurn, MemoryStore, StoredTurn


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    """一次性初始化好的 SessionDB，tmp_path 隔离。"""
    session_db = SessionDB(tmp_path / "state.db")
    await session_db.initialize()
    yield session_db
    await session_db.close()


# ---- 3.1.a 生命周期 --------------------------------------------------


@pytest.mark.asyncio
async def test_initialize_is_idempotent(tmp_path: Path):
    db = SessionDB(tmp_path / "state.db")
    await db.initialize()
    await db.initialize()  # 第二次不应抛也不应重复迁移
    assert db._initialized is True
    await db.close()


@pytest.mark.asyncio
async def test_initialize_enables_wal_mode(tmp_path: Path):
    db = SessionDB(tmp_path / "state.db")
    await db.initialize()
    # 直接 open 同步 conn 验证 journal_mode 已切到 wal
    conn = sqlite3.connect(tmp_path / "state.db")
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
    finally:
        conn.close()
    assert mode == "wal"
    await db.close()


# ---- 3.1.b CRUD ------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_returns_uuid(db: SessionDB):
    sid = await db.create_session({"origin": "pytest"})
    assert isinstance(sid, str)
    # UUID 标准形式 36 字符（含 4 个短横）
    assert len(sid) == 36 and sid.count("-") == 4


@pytest.mark.asyncio
async def test_append_and_get_messages(db: SessionDB):
    sid = await db.create_session()
    id1 = await db.append_message(sid, "user", "hello world")
    id2 = await db.append_message(sid, "assistant", "hi there, python user")
    assert id2 > id1

    msgs = await db.get_messages(sid, limit=10)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "hello world"
    assert msgs[0]["id"] == id1
    # salience 默认 0.5
    assert msgs[0]["salience"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_memory_store_protocol_and_admin_surface(db: SessionDB):
    assert isinstance(db, MemoryStore)

    await db.append("proto-a", "user", "hello from protocol")
    await db.append("proto-a", "assistant", "reply from protocol")
    await db.append("proto-b", "user", "other session")

    recent = await db.get_recent("proto-a", limit=10)
    assert all(isinstance(turn, ConversationTurn) for turn in recent)
    assert [(turn.role, turn.content) for turn in recent] == [
        ("user", "hello from protocol"),
        ("assistant", "reply from protocol"),
    ]
    assert recent[0].created_at <= recent[1].created_at

    turns = await db.list_turns("proto-a")
    assert all(isinstance(turn, StoredTurn) for turn in turns)
    assert [turn.content for turn in turns] == [
        "hello from protocol",
        "reply from protocol",
    ]

    assert await db.delete_turn(turns[0].id) is True
    assert await db.delete_turn(turns[0].id) is False
    assert [turn.content for turn in await db.list_turns("proto-a")] == [
        "reply from protocol"
    ]

    sessions = await db.list_sessions()
    by_id = {session.session_id: session for session in sessions}
    assert by_id["proto-a"].turn_count == 1
    assert by_id["proto-b"].turn_count == 1

    await db.clear("proto-a")
    assert await db.get_recent("proto-a") == []
    assert await db.clear_all() == 1
    assert await db.list_turns(None) == []


@pytest.mark.asyncio
async def test_get_messages_pagination(db: SessionDB):
    sid = await db.create_session()
    for i in range(5):
        await db.append_message(sid, "user", f"msg-{i}")
    first_two = await db.get_messages(sid, limit=2, offset=0)
    last_three = await db.get_messages(sid, limit=10, offset=2)
    assert [m["content"] for m in first_two] == ["msg-0", "msg-1"]
    assert [m["content"] for m in last_three] == ["msg-2", "msg-3", "msg-4"]


# ---- 3.4 FTS5 triggers ----------------------------------------------


@pytest.mark.asyncio
async def test_fts5_insert_indexed_immediately(db: SessionDB):
    sid = await db.create_session()
    await db.append_message(sid, "user", "hello world python")
    await db.append_message(sid, "user", "completely unrelated content")
    hits = await db.search_fts("python")
    assert len(hits) == 1
    assert hits[0]["content"] == "hello world python"
    # rank 列应存在（FTS5 built-in）
    assert "rank" in hits[0]


@pytest.mark.asyncio
async def test_fts5_delete_removes_from_index(db: SessionDB, tmp_path: Path):
    sid = await db.create_session()
    msg_id = await db.append_message(sid, "user", "transient content xyzzy")
    hits = await db.search_fts("xyzzy")
    assert len(hits) == 1

    # 直接用 sqlite3 DELETE（触发器应同步移除 fts 索引项）
    conn = sqlite3.connect(tmp_path / "state.db")
    try:
        conn.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
        conn.commit()
    finally:
        conn.close()

    hits_after = await db.search_fts("xyzzy")
    assert hits_after == []


@pytest.mark.asyncio
async def test_fts5_update_re_indexes(db: SessionDB, tmp_path: Path):
    sid = await db.create_session()
    msg_id = await db.append_message(sid, "user", "original term alpha")
    assert len(await db.search_fts("alpha")) == 1

    # 改内容：触发 messages_au trigger
    conn = sqlite3.connect(tmp_path / "state.db")
    try:
        conn.execute(
            "UPDATE messages SET content = ? WHERE id = ?",
            ("replaced text beta", msg_id),
        )
        conn.commit()
    finally:
        conn.close()

    assert await db.search_fts("alpha") == []
    hits = await db.search_fts("beta")
    assert len(hits) == 1
    assert hits[0]["content"] == "replaced text beta"


@pytest.mark.asyncio
async def test_fts5_chinese_and_english(db: SessionDB):
    """FTS5 trigram 分词：≥3 字符的中英文子串都能 MATCH。

    spec 要求 "一起学 python" → search_fts("python") 能命中；
    trigram 额外让"纯中文"这类无空格中文也能子串召回。
    """
    sid = await db.create_session()
    await db.append_message(sid, "user", "一起学 python")
    await db.append_message(sid, "user", "我喜欢 go 语言")
    await db.append_message(sid, "user", "纯中文的句子")

    py_hits = await db.search_fts("python")
    assert len(py_hits) == 1
    assert "python" in py_hits[0]["content"]

    # trigram 需要 ≥3 字符才能匹配（这是 trigram 的本质约束，≤2 字符
    # 无 3-gram 单元可比较）。agent 实际 query 长度几乎都 ≥3。
    zh_hits = await db.search_fts("中文的")
    assert len(zh_hits) == 1
    assert zh_hits[0]["content"] == "纯中文的句子"

    # 3 字符英文子串也应命中
    prefix_hits = await db.search_fts("pyt")
    assert any("python" in h["content"] for h in prefix_hits)


@pytest.mark.asyncio
async def test_search_fts_filters_by_session_id(db: SessionDB):
    sid_a = await db.create_session()
    sid_b = await db.create_session()
    await db.append_message(sid_a, "user", "alpha session one")
    await db.append_message(sid_b, "user", "alpha session two")

    all_hits = await db.search_fts("alpha")
    assert len(all_hits) == 2

    only_a = await db.search_fts("alpha", session_id=sid_a)
    assert len(only_a) == 1
    assert only_a[0]["session_id"] == sid_a


# ---- 3.1.c salience 更新 --------------------------------------------


@pytest.mark.asyncio
async def test_update_salience_touches_timestamp(db: SessionDB):
    sid = await db.create_session()
    mid = await db.append_message(sid, "user", "salience target")

    before = (await db.get_messages(sid))[0]
    assert before["decay_last_touch"] is None
    assert before["salience"] == pytest.approx(0.5)

    await db.update_salience(mid, 0.55, touch=True)
    after = (await db.get_messages(sid))[0]
    assert after["salience"] == pytest.approx(0.55)
    assert after["decay_last_touch"] is not None


@pytest.mark.asyncio
async def test_update_salience_without_touch(db: SessionDB):
    sid = await db.create_session()
    mid = await db.append_message(sid, "user", "salience no-touch")
    # 先 touch 一次留下时间戳
    await db.update_salience(mid, 0.6, touch=True)
    first_touch = (await db.get_messages(sid))[0]["decay_last_touch"]

    await db.update_salience(mid, 0.7, touch=False)
    row = (await db.get_messages(sid))[0]
    assert row["salience"] == pytest.approx(0.7)
    # touch=False 不应改 decay_last_touch
    assert row["decay_last_touch"] == first_touch


# ---- 3.1.d tool_calls JSON roundtrip --------------------------------


@pytest.mark.asyncio
async def test_tool_calls_json_roundtrip(db: SessionDB):
    sid = await db.create_session()
    calls = [
        {"id": "call_1", "type": "function", "function": {"name": "web_fetch", "arguments": "{}"}},
    ]
    mid = await db.append_message(
        sid, "assistant", "thinking...", tool_calls=calls
    )
    msgs = await db.get_messages(sid)
    assert len(msgs) == 1
    stored = msgs[0]["tool_calls"]
    assert isinstance(stored, list)
    assert stored[0]["id"] == "call_1"
    assert stored[0]["function"]["name"] == "web_fetch"
    assert msgs[0]["id"] == mid


@pytest.mark.asyncio
async def test_tool_call_id_persists_for_tool_role(db: SessionDB):
    sid = await db.create_session()
    await db.append_message(
        sid, "tool", "{\"ok\":true}", tool_call_id="call_xyz"
    )
    msgs = await db.get_messages(sid)
    assert msgs[0]["tool_call_id"] == "call_xyz"
    assert msgs[0]["role"] == "tool"


# ---- busy retry helper (unit-level，不强依赖并发) -------------------


@pytest.mark.asyncio
async def test_busy_retry_helper_classifies_errors(tmp_path: Path):
    """验证 _is_busy_error 的分类逻辑 —— 正向/反向样本各一。"""
    from deskpet.memory.session_db import _is_busy_error

    busy = sqlite3.OperationalError("database is locked")
    other = sqlite3.OperationalError("no such table: ghost")
    not_op = ValueError("unrelated")

    assert _is_busy_error(busy) is True
    assert _is_busy_error(other) is False
    assert _is_busy_error(not_op) is False
