# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""FATAL-A 回归 — 自动 backfill 安全网（2026-06-02 记忆系统审计 #1）。

## 背景
审计发现致命缺陷 FATAL-A：`VectorWorker.backfill_missing()` 此前**只**在手动
脚本（`scripts/backfill_vectors.py`）/ eval / 测试里调用，**lifespan 启动路径
从不调用**。后果：任何 embedding 缺口都会让 `messages.embedding` 永久 IS NULL
且无声 —— 向量召回永远漏掉这些消息（"刚说的话下次不记得"）。缺口来源：
- `_wait_for_drain` TOCTOU 竞态导致 stop 时丢最后一批（hunter FATAL-2）
- `_flush` encode 失败 → `_failed++; return`，该批留 NULL（hunter HIGH-4）
- embedder 子进程崩溃

桌宠用户永远不会手动跑脚本 → 缺口永久。修复：lifespan 在 worker 起来后
fire-and-forget 跑一次 `backfill_missing()`（main.py `_vector_backfill_bg`）。

## 本文件验什么
1. **行为**（核心安全网证明）：messages 表里有 `embedding IS NULL` 的缺口时，
   `backfill_missing()` 把它们全部回填且可向量召回。这是 FATAL-A 修复**依赖**
   的机制 —— 若它坏了，lifespan 的兜底也救不了。
2. **wiring 存在性守卫**：main.py lifespan 在 `_vw.start()` 后确实接了
   `_vector_backfill_bg` → `backfill_missing()`。这是防止有人删掉兜底接线的
   回归闸（无法在不启动整个 app 的前提下做行为级 lifespan 测试，故用源级守卫）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio

from deskpet.memory.embedder import Embedder
from deskpet.memory.session_db import SessionDB
from deskpet.memory.vector_worker import VectorWorker


@pytest_asyncio.fixture
async def session_db(tmp_path: Path):
    db = SessionDB(tmp_path / "state.db")
    await db.initialize()
    yield db
    await db.close()


@pytest_asyncio.fixture
async def mock_embedder(tmp_path: Path):
    e = Embedder(model_path=tmp_path / "no-bge", use_mock_when_missing=True)
    await e.warmup()
    yield e
    await e.close()


def _count_embedding_blobs(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE embedding IS NOT NULL"
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()


# ----------------------------------------------------------------------
# 1. 行为：backfill 回填 NULL embedding 缺口（安全网核心）
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fatal_a_backfill_recovers_null_embedding_gap(
    session_db: SessionDB, mock_embedder: Embedder
) -> None:
    """模拟 FATAL-A 缺口：消息在 messages 表但 embedding 全 NULL（worker 丢了/
    没跑）→ backfill_missing 把它们全部回填。

    这正是 lifespan 兜底任务依赖的机制。append_message 本身不嵌入（嵌入靠
    worker.enqueue，此处故意不 enqueue 来制造缺口）。
    """
    sid = await session_db.create_session()
    n = 6
    for i in range(n):
        await session_db.append_message(
            sid, "user", f"缺口消息 {i}：关于猫和写代码的事 gap-{i}"
        )

    # FATAL-A 缺口现状：消息都在，但 embedding 全 NULL（向量召回漏掉它们）
    assert _count_embedding_blobs(session_db._db_path) == 0, (
        "前提：append_message 不应自动嵌入（嵌入靠 worker）"
    )

    # 安全网：backfill 回填
    worker = VectorWorker(mock_embedder, session_db, batch_size=4)
    processed = await worker.backfill_missing()

    assert processed == n, f"backfill 应处理全部 {n} 条缺口，实际 {processed}"
    assert _count_embedding_blobs(session_db._db_path) == n, (
        f"backfill 后全部 {n} 条应有 embedding（缺口闭合）"
    )


@pytest.mark.asyncio
async def test_fatal_a_backfill_is_idempotent_no_gap(
    session_db: SessionDB, mock_embedder: Embedder
) -> None:
    """无缺口时 backfill 返回 0（幂等，不重复嵌入）→ lifespan 反复跑安全。"""
    sid = await session_db.create_session()
    for i in range(3):
        await session_db.append_message(sid, "user", f"m{i}")
    worker = VectorWorker(mock_embedder, session_db, batch_size=4)
    first = await worker.backfill_missing()
    assert first == 3
    # 再跑一次：已无 NULL → 0（lifespan 每次启动跑，不能重复做功）
    second = await worker.backfill_missing()
    assert second == 0, f"无缺口时 backfill 应返回 0，实际 {second}"


# ----------------------------------------------------------------------
# 2. wiring 存在性守卫：lifespan 确实接了自动 backfill
# ----------------------------------------------------------------------
def test_fatal_a_lifespan_wires_auto_backfill() -> None:
    """守卫：main.py lifespan 在 vector worker 起来后接了 _vector_backfill_bg
    → backfill_missing()。

    无法在不启动整个 app（ASR/TTS/LLM/MCP）的前提下做行为级 lifespan 测试，
    故用源级守卫防止兜底接线被误删 —— 这是 FATAL-A 的核心修复点，删了就回到
    "缺口永久无声"的致命状态。
    """
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    src = main_py.read_text(encoding="utf-8")

    # 兜底任务定义 + 调用都在
    assert "_vector_backfill_bg" in src, (
        "FATAL-A 兜底任务 _vector_backfill_bg 不见了 —— 自动 backfill 被删？"
    )
    assert "backfill_missing()" in src, (
        "lifespan 不再调 backfill_missing() —— 回到缺口永久无声的致命状态"
    )
    assert "asyncio.create_task(_vector_backfill_bg())" in src, (
        "兜底任务定义了但没 create_task 起来 —— 等于没接"
    )

    # 顺序守卫：backfill 接线必须在 vector worker start 之后（_vw.start() 之后）。
    # 注意：用真正的 create_task 调用点定位（`_vector_backfill_bg` 这个名字在
    # main.py:810 注释里也出现一次，naive find 会命中注释 → 误判，故锁 create_task）。
    start_idx = src.find("await _vw.start()")
    wire_idx = src.find("asyncio.create_task(_vector_backfill_bg())")
    assert start_idx != -1 and wire_idx != -1
    assert wire_idx > start_idx, (
        "自动 backfill 接线应在 _vw.start() 之后（worker 就绪才能 backfill）"
    )
