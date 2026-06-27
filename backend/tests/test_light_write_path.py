# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""FP-4 WI-3.4 — 写入分级 light 快路单测。

契约验证：
  TG-1  append_message(skip_embed=True)  → hook 不触发 (call_count == 0)
        消息仍写入 messages 表。
  TG-2  append_message(skip_embed=False) 默认  → hook 照常触发 (BC 回归)。
  TG-3  light 消息在 messages 表 + FTS 可召回 (L2)；
        不写 messages_vec（embedding 列 IS NULL）。
  TG-4  MemoryV2Config.light_write=False → skip_embed 参数默认 False，
        即高频流也会 embed（保守回退，flag 关闭等于当前行为）。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from deskpet.memory.session_db import SessionDB


# ─── 共用 fixture ────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def plain_db(tmp_path: Path):
    """无 hook 的裸 SessionDB（skip_embed 参数的基础测试用）。"""
    db = SessionDB(tmp_path / "light.db")
    await db.initialize()
    yield db
    await db.close()


@pytest_asyncio.fixture
async def hooked_db(tmp_path: Path):
    """带 AsyncMock hook 的 SessionDB，用于断言 hook 调用次数。"""
    hook = AsyncMock()
    db = SessionDB(tmp_path / "hooked_light.db", on_message_written=hook)
    await db.initialize()
    yield db, hook
    await db.close()


# ─── TG-1: skip_embed=True → hook 不触发 ────────────────────────────────────


@pytest.mark.asyncio
async def test_skip_embed_true_hook_not_called(hooked_db):
    """TG-1: skip_embed=True 时 hook 完全不被调用。"""
    db, hook = hooked_db
    sid = await db.create_session()

    mid = await db.append_message(
        sid, "user", "light tick content", skip_embed=True
    )

    # hook 必须 call_count == 0
    assert hook.call_count == 0, (
        f"hook should NOT be called when skip_embed=True, got call_count={hook.call_count}"
    )
    # msg_id 必须有效
    assert isinstance(mid, int) and mid > 0


@pytest.mark.asyncio
async def test_skip_embed_true_message_still_in_db(hooked_db):
    """TG-1 附加：skip_embed=True 时消息仍写入 messages 表。"""
    db, hook = hooked_db
    sid = await db.create_session()

    await db.append_message(sid, "user", "light content persisted", skip_embed=True)

    msgs = await db.get_messages(sid)
    assert len(msgs) == 1
    assert msgs[0]["content"] == "light content persisted"


@pytest.mark.asyncio
async def test_skip_embed_mixed_calls(hooked_db):
    """TG-1 扩展：同一 session 里 light + normal 混合，hook 仅在 normal 消息触发。"""
    db, hook = hooked_db
    sid = await db.create_session()

    await db.append_message(sid, "user", "tick-1", skip_embed=True)
    await db.append_message(sid, "user", "tick-2", skip_embed=True)
    mid_normal = await db.append_message(sid, "user", "full utterance", skip_embed=False)
    await db.append_message(sid, "user", "tick-3", skip_embed=True)

    # 只有那 1 条 normal 消息触发 hook
    assert hook.call_count == 1
    hook.assert_called_once_with(mid_normal, "full utterance")


# ─── TG-2: skip_embed=False（默认）→ hook 照常触发（BC） ────────────────────


@pytest.mark.asyncio
async def test_skip_embed_false_default_hook_called(hooked_db):
    """TG-2: 默认 skip_embed=False 时 hook 照常被调用（BC 回归）。"""
    db, hook = hooked_db
    sid = await db.create_session()

    mid = await db.append_message(sid, "user", "normal utterance")

    assert hook.call_count == 1
    hook.assert_called_once_with(mid, "normal utterance")


@pytest.mark.asyncio
async def test_skip_embed_false_explicit_hook_called(hooked_db):
    """TG-2: 显式传 skip_embed=False 与默认行为一致（BC 回归）。"""
    db, hook = hooked_db
    sid = await db.create_session()

    mid = await db.append_message(
        sid, "assistant", "reply text", skip_embed=False
    )

    assert hook.call_count == 1
    hook.assert_called_once_with(mid, "reply text")


@pytest.mark.asyncio
async def test_bc_multiple_messages_hook_called_each(hooked_db):
    """TG-2: 多条 normal 消息各触发一次 hook（BC 计数回归）。"""
    db, hook = hooked_db
    sid = await db.create_session()

    for i in range(5):
        await db.append_message(sid, "user", f"msg-{i}")

    assert hook.call_count == 5


@pytest.mark.asyncio
async def test_no_hook_skip_embed_true_is_noop(plain_db):
    """TG-2 / BC: 无 hook 时 skip_embed=True 仍正常写入（无 AttributeError）。"""
    sid = await plain_db.create_session()
    mid = await plain_db.append_message(sid, "user", "no hook path", skip_embed=True)
    assert isinstance(mid, int) and mid > 0
    msgs = await plain_db.get_messages(sid)
    assert msgs[0]["content"] == "no hook path"


# ─── TG-3: light 消息在 L2 (messages + FTS) 可召回；embedding IS NULL ────────


@pytest.mark.asyncio
async def test_light_message_in_messages_table(plain_db):
    """TG-3a: light 消息进 messages 表（L2 可查）。"""
    sid = await plain_db.create_session()
    mid = await plain_db.append_message(
        sid, "user", "fts-searchable light", skip_embed=True
    )

    msgs = await plain_db.get_messages(sid)
    ids = [m["id"] for m in msgs]
    assert mid in ids


@pytest.mark.asyncio
async def test_light_message_fts_searchable(plain_db):
    """TG-3b: light 消息通过 FTS5 trigger 自动同步，search_fts 可召回。"""
    sid = await plain_db.create_session()
    unique_token = "xyzlighttokenuniq"
    await plain_db.append_message(
        sid, "user", f"this is a {unique_token} message", skip_embed=True
    )

    results = await plain_db.search_fts(unique_token)
    assert len(results) >= 1
    contents = [r["content"] for r in results]
    assert any(unique_token in c for c in contents)


@pytest.mark.asyncio
async def test_light_message_embedding_is_null(plain_db):
    """TG-3c: light 消息的 messages.embedding IS NULL（没有进向量）。"""
    import aiosqlite

    sid = await plain_db.create_session()
    mid = await plain_db.append_message(
        sid, "user", "no embedding expected", skip_embed=True
    )

    async with aiosqlite.connect(plain_db._db_path) as conn:
        cursor = await conn.execute(
            "SELECT embedding FROM messages WHERE id = ?", (mid,)
        )
        row = await cursor.fetchone()
        await cursor.close()

    assert row is not None, f"message {mid} not found in messages table"
    assert row[0] is None, (
        f"expected embedding IS NULL for light message, got: {row[0]!r}"
    )


@pytest.mark.asyncio
async def test_normal_message_embedding_also_null_without_worker(plain_db):
    """TG-3d: 无 VectorWorker 时，normal 消息 embedding 也 NULL（hook 管理列，
    messages 表本身不自动填 embedding）。这确认 embedding NULL 是 DB schema 默认
    而非 skip_embed 的副作用 —— light 路的区别只在于 hook 不触发。"""
    import aiosqlite

    sid = await plain_db.create_session()
    mid = await plain_db.append_message(sid, "user", "no worker attached")

    async with aiosqlite.connect(plain_db._db_path) as conn:
        cursor = await conn.execute(
            "SELECT embedding FROM messages WHERE id = ?", (mid,)
        )
        row = await cursor.fetchone()
        await cursor.close()

    assert row is not None
    assert row[0] is None  # 无 worker → embedding 仍 NULL（schema 默认）


# ─── TG-4: MemoryV2Config.light_write flag ───────────────────────────────────


def test_memory_v2_config_light_write_default_false():
    """TG-4a: MemoryV2Config.light_write 默认 False（flag 关闭 = 当前行为不变）。"""
    from config import MemoryV2Config

    cfg = MemoryV2Config()
    assert cfg.light_write is False, (
        f"light_write should default to False, got {cfg.light_write!r}"
    )


def test_memory_v2_config_light_write_can_be_enabled():
    """TG-4b: MemoryV2Config.light_write=True 可正常构造（flag 开启路径）。"""
    from config import MemoryV2Config

    cfg = MemoryV2Config(light_write=True)
    assert cfg.light_write is True


def test_memory_v2_config_light_write_false_means_skip_embed_false():
    """TG-4c: flag=False 时，skip_embed 参数的默认值为 False
    （即 append_message 默认行为不变）。

    验证方式：SessionDB.append_message 的 skip_embed 默认值 = False。
    调用方若不传 skip_embed，等同于 skip_embed=False，hook 总是触发。
    这与 flag=False 时"高频流也 embed = 当前行为"完全吻合。
    """
    import inspect
    from deskpet.memory.session_db import SessionDB

    sig = inspect.signature(SessionDB.append_message)
    param = sig.parameters.get("skip_embed")
    assert param is not None, "append_message must have skip_embed parameter"
    assert param.default is False, (
        f"skip_embed default must be False (BC), got {param.default!r}"
    )


@pytest.mark.asyncio
async def test_flag_false_hook_always_fires(hooked_db):
    """TG-4d: flag=False 时任何消息都触发 hook（等效于 skip_embed 从不被置 True）。

    此测试模拟"调用方读 flag → 不传 skip_embed=True"的保守路径。
    """
    from config import MemoryV2Config

    db, hook = hooked_db
    cfg = MemoryV2Config()  # light_write=False

    sid = await db.create_session()

    # 调用方尊重 flag=False：无论何种消息都不传 skip_embed=True
    effective_skip = cfg.light_write  # False
    await db.append_message(sid, "user", "msg-a", skip_embed=effective_skip)
    await db.append_message(sid, "user", "msg-b", skip_embed=effective_skip)

    # flag=False → effective_skip=False → hook 对每条都触发
    assert hook.call_count == 2


# ─── WI-OH-3: MemoryManager.write(light=) 透传 + 高频流来源门控 ──────────────


def _make_manager(session_db):
    """构造一个仅用于 write 路径测试的 MemoryManager（L1/L3 用占位）。"""
    from deskpet.memory.manager import MemoryManager

    class _StubFileMemory:
        async def append(self, *a, **k):  # pragma: no cover - 不走 L1
            return None

    return MemoryManager(_StubFileMemory(), session_db, retriever=None)


@pytest.mark.asyncio
async def test_manager_write_light_true_skips_hook(hooked_db):
    """OH-3: manager.write(target='session', light=True) → skip_embed=True
    → on_message_written hook 不触发 → VectorWorker 队列不增长。"""
    db, hook = hooked_db
    mgr = _make_manager(db)
    sid = await db.create_session()

    await mgr.write(
        "voice vad tick", target="session", session_id=sid, role="user", light=True
    )

    assert hook.call_count == 0, "light=True 应跳 hook（不进 VectorWorker 队列）"
    msgs = await db.get_messages(sid)
    assert len(msgs) == 1 and msgs[0]["content"] == "voice vad tick"


@pytest.mark.asyncio
async def test_manager_write_light_default_false_fires_hook(hooked_db):
    """OH-3 BC: manager.write 默认 light=False → skip_embed=False → hook 照常。"""
    db, hook = hooked_db
    mgr = _make_manager(db)
    sid = await db.create_session()

    await mgr.write("full utterance", target="session", session_id=sid, role="user")

    assert hook.call_count == 1
    hook.assert_called_once_with(1, "full utterance")


@pytest.mark.asyncio
async def test_manager_put_doc_light_alias(hooked_db):
    """OH-3: put_doc_light 命名快路 = write(light=True)（跳 hook）。"""
    db, hook = hooked_db
    mgr = _make_manager(db)
    sid = await db.create_session()

    await mgr.put_doc_light("screenshot low-info verdict", session_id=sid)

    assert hook.call_count == 0
    msgs = await db.get_messages(sid)
    assert msgs[0]["role"] == "user"


@pytest.mark.asyncio
async def test_high_frequency_source_flag_on_skips_embed(hooked_db):
    """OH-3: 高频流来源 + flag ON → 调用方计算 light=flag AND 高频 →
    skip_embed → VectorWorker 队列（= hook call_count）不增长。"""
    from config import MemoryV2Config

    db, hook = hooked_db
    mgr = _make_manager(db)
    sid = await db.create_session()
    cfg = MemoryV2Config(light_write=True)

    # 调用方语义：来源是高频流 → light = flag AND is_high_freq
    is_high_freq = True
    effective_light = cfg.light_write and is_high_freq  # True
    await mgr.write(
        "vad-tick-1", target="session", session_id=sid, role="user", light=effective_light
    )
    await mgr.write(
        "vad-tick-2", target="session", session_id=sid, role="user", light=effective_light
    )

    # 两条高频流消息都被跳过 → hook（VectorWorker enqueue）从未触发
    assert hook.call_count == 0


@pytest.mark.asyncio
async def test_high_frequency_source_flag_off_all_embed_bc(hooked_db):
    """OH-3 BC: flag OFF → 即便来源是高频流，light=flag AND 高频 = False →
    所有写入 skip_embed=False → hook 每条都触发（字节级当前行为）。"""
    from config import MemoryV2Config

    db, hook = hooked_db
    mgr = _make_manager(db)
    sid = await db.create_session()
    cfg = MemoryV2Config()  # light_write=False

    is_high_freq = True
    effective_light = cfg.light_write and is_high_freq  # False（flag OFF 压制）
    await mgr.write(
        "vad-tick-a", target="session", session_id=sid, role="user", light=effective_light
    )
    await mgr.write(
        "vad-tick-b", target="session", session_id=sid, role="user", light=effective_light
    )

    # flag OFF → 全部 embed → hook 每条都触发
    assert hook.call_count == 2
