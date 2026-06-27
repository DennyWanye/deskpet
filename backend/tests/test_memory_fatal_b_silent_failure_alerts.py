# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""FATAL-B 回归 — 检索/嵌入静默降级告警（2026-06-02 记忆系统审计 #2）。

## 背景
审计 + silent-failure-hunter + 业界 RAG 反模式调研三方互证：检索热路径的降级
**全程无告警** —— "缺失的 BM25 层不报错，只是悄悄漏掉点名查询……上线 90 分一个
季度掉到 60 分"。具体静默点（改前只 log.debug 或无 log）：
- `retriever._fts_recall` 吞 `OperationalError` → log.debug（gen-1 **活路径**，
  FTS 一路静默失效，向量+recency+salience 还在跑，用户/开发都不察觉）
- `facts._embed_fact` encode/serialize 失败 → log.debug（embedding 留 NULL，
  fact 静默退化成只能 LIKE 召回；facts 无 backfill 比 messages 更糟）
- `enhanced_retriever._collect_fact_hits` 向量→LIKE 降级无 log

修复：这些降级点 log.debug → log.warning（真降级该可见），降级路径加 debug。

## 本文件验什么
用 stdlib `caplog`（内存模块用 logging.getLogger，非 structlog → caplog 可抓）
断言：触发降级时**确实发出了 WARNING**，且功能仍优雅降级（返回 []/None 不抛）。
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio

from deskpet.memory.facts import FactsStore
from deskpet.memory.retriever import Retriever
from deskpet.memory.session_db import SessionDB


class _BoomEmbedder:
    """非 mock embedder，encode 必抛 —— 模拟子进程崩 / CUDA OOM。

    不是 mock（is_mock→False）→ _embed_fact 不会提前 return，会真的走 encode
    然后撞异常，触发我们要测的 warning。
    """

    def is_mock(self) -> bool:
        return False

    def is_ready(self) -> bool:
        return True

    async def encode(self, texts):
        raise RuntimeError("embedder subprocess boom")


@pytest_asyncio.fixture
async def session_db(tmp_path: Path):
    db = SessionDB(tmp_path / "state.db")
    await db.initialize()
    yield db
    await db.close()


# ----------------------------------------------------------------------
# 1. retriever FTS 路失效 → WARNING（gen-1 活路径，FATAL-B 核心）
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fatal_b_fts_failure_logs_warning(
    session_db: SessionDB, caplog
) -> None:
    """FTS search 抛 OperationalError → _fts_recall 优雅返 [] 且发 WARNING。

    改前只 log.debug → FTS 一路静默掉，无人察觉。改后 warning 可见。
    """
    r = Retriever(session_db=session_db, embedder=None)

    async def _boom(*_a, **_k):
        raise sqlite3.OperationalError("fts5: syntax error near ...")

    r._db.search_fts = _boom  # type: ignore[assignment]

    with caplog.at_level(logging.WARNING, logger="deskpet.memory.retriever"):
        out = await r._fts_recall("点名查询 keyword", 10)

    assert out == [], "FTS 失效应优雅降级为空，不抛"
    warns = [
        rec for rec in caplog.records
        if rec.levelno >= logging.WARNING and "fts search failed" in rec.message
    ]
    assert warns, f"FTS 失效必须发 WARNING（改前是静默 debug）: {caplog.text}"


# ----------------------------------------------------------------------
# 2. facts._embed_fact 失败 → WARNING（embedding 静默留 NULL）
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fatal_b_facts_embed_failure_logs_warning(
    tmp_path: Path, caplog
) -> None:
    """embedder 崩 → _embed_fact 返 None（embedding NULL）且发 WARNING。

    改前只 log.debug → fact 静默退化成只能 LIKE 召回，无信号。
    """
    fs = FactsStore(tmp_path / "f.db", embedder=_BoomEmbedder())

    with caplog.at_level(logging.WARNING, logger="deskpet.memory.facts"):
        result = await fs._embed_fact("name", "李明")

    assert result is None, "embed 失败应返 None（embedding 留 NULL）"
    warns = [
        rec for rec in caplog.records
        if rec.levelno >= logging.WARNING and "embed failed" in rec.message
    ]
    assert warns, f"embedding 写失败必须发 WARNING（改前静默 debug）: {caplog.text}"


@pytest.mark.asyncio
async def test_fatal_b_embed_no_embedder_is_silent_by_design(
    tmp_path: Path, caplog
) -> None:
    """对照：无 embedder（None）是**有意降级**，不该发 WARNING（避免误报噪声）。

    确保我们只对"真失败"告警，不对"本来就没配 embedder"的正常降级刷 warning。
    """
    fs = FactsStore(tmp_path / "f.db", embedder=None)
    with caplog.at_level(logging.WARNING, logger="deskpet.memory.facts"):
        result = await fs._embed_fact("name", "李明")
    assert result is None
    assert not [
        rec for rec in caplog.records if rec.levelno >= logging.WARNING
    ], "无 embedder 是有意降级，不该发 WARNING（防噪声）"


# ----------------------------------------------------------------------
# 3. enhanced_retriever facts 向量召回炸 → 降级 LIKE 且 WARNING（hunter HIGH-6）
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fatal_b_facts_vector_degradation_logs_warning(
    session_db: SessionDB, tmp_path: Path, caplog
) -> None:
    """facts 向量召回炸 → _collect_fact_hits 降级 LIKE 且发 WARNING。

    改前：向量→LIKE 降级**无任何 log**，facts 召回静默退化无人察觉（HIGH-6）。
    """
    from deskpet.memory.enhanced_retriever import EnhancedRetriever

    fs = FactsStore(tmp_path / "f.db", embedder=None)  # LIKE 兜底用（空表）
    base = Retriever(session_db=session_db, embedder=None)
    er = EnhancedRetriever(base, facts_store=fs, embedder=_BoomEmbedder())

    with caplog.at_level(
        logging.WARNING, logger="deskpet.memory.enhanced_retriever"
    ):
        hits = await er._collect_fact_hits("宠物名字", top_k=5)

    assert hits == [], "向量炸 → 降级 LIKE，空 facts 表 → []，优雅不抛"
    warns = [
        rec for rec in caplog.records
        if rec.levelno >= logging.WARNING and "vector_search failed" in rec.message
    ]
    assert warns, f"facts 向量降级必须发 WARNING（改前无任何 log）: {caplog.text}"
