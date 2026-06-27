# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""G6 — 写入闭环边界测试（记忆系统严测 Phase 4，P2）。

## 背景
写入路径现有覆盖 95%（facts upsert / merge / workspace 追踪都测过），G6 补
边界与不变量：
- facts upsert 在 embedder 可用时**真写入 embedding 列**（向量召回的前提）
- workspace 大文件 hash 稳定 + 摘要按 max_chars 截断 + byte_size 准确
- 并发写入同 (session,path) 不丢失、不产生重复主键错

不追求穷举，聚焦"之前没断言到的写入端不变量"。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

import deskpet.memory.memory_v2_schema as _schema
from deskpet.memory.embedder import Embedder
from deskpet.memory.facts import FactsStore
from deskpet.memory.workspace import WorkspaceMemoryStore, _content_hash


@pytest.fixture(autouse=True)
def _reset_schema_cache() -> None:
    """每个测试重置 memory_v2_schema 的全局缓存 + 重建 _lock。

    memory_v2_schema 用模块级 ``_lock = asyncio.Lock()`` + ``_ensured`` 做
    schema 初始化的双检锁。生产是单事件循环 → _lock 绑定一次永久有效，
    并发 ensure 正确串行化（设计如此）。但 pytest-asyncio 是 function-scope
    loop（每测试新 loop），首个 FactsStore 测试把 _lock 绑到它的 loop，
    后续测试在新 loop 里 ``async with _lock`` → "bound to a different event
    loop"。重建 _lock（在测试自身 loop 首次 await 时绑定）忠实复现"每次新
    进程新 loop"的生产不变量，纯测试隔离，不掩盖生产问题。
    """
    _schema._reset_cache_for_tests()
    _schema._lock = asyncio.Lock()
    yield
    _schema._reset_cache_for_tests()


@pytest_asyncio.fixture
async def ws_store(tmp_path: Path) -> WorkspaceMemoryStore:
    return WorkspaceMemoryStore(tmp_path / "ws.db")


# ----------------------------------------------------------------------
# G6.1 — facts upsert 在真 embedder 下真写入 embedding 列
# ----------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.model_required
async def test_g6_1_facts_upsert_writes_embedding_real(tmp_path: Path) -> None:
    """真 BGE-M3：upsert 后 embedding 列非空（向量召回的前提）。

    若 embedding 没写入，vector_search 会跳过该行 → 向量召回永远空（F5 ②
    层就失效）。这条钉死"写入端真的算并存了向量"。
    """
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _model_path import resolve_bge_m3

    model = resolve_bge_m3()
    if model is None:
        pytest.skip("BGE-M3 模型未找到；装模型或设 DESKPET_BGE_M3_DIR 后再跑")
    e = Embedder(model_path=model, use_mock_when_missing=False)
    await e.warmup()
    try:
        fs = FactsStore(tmp_path / "f.db", embedder=e)
        fid = await fs.upsert(
            category="profile", subject="user", key="pet",
            value="我家养了一只橘猫", confidence=0.9,
            source_msg_id=1, evidence="x",
        )
        row = await fs.get_by_id(int(fid))
        assert row is not None
        emb = row.get("embedding")
        assert emb, "真 embedder 下 upsert 必须写入 embedding 列（非空）"
        # embedding 是 float32 blob，1024 维 → 4096 bytes
        assert len(emb) == 1024 * 4, (
            f"embedding 应为 1024 维 float32 (4096 bytes)，实际 {len(emb)}"
        )
    finally:
        await e.close()


@pytest.mark.asyncio
async def test_g6_1b_facts_upsert_mock_no_embedding(tmp_path: Path) -> None:
    """对照：mock embedder（或无 embedder）下 upsert 不写 embedding（降级 LIKE）。"""
    fs = FactsStore(tmp_path / "f.db", embedder=None)
    fid = await fs.upsert(
        category="profile", subject="user", key="pet",
        value="我家养了一只橘猫", confidence=0.9,
        source_msg_id=1, evidence="x",
    )
    row = await fs.get_by_id(int(fid))
    assert row is not None
    # 无 embedder → embedding 列为空 → 召回端降级 LIKE（设计如此）
    assert not row.get("embedding"), "无 embedder 时不该写 embedding 列"


# ----------------------------------------------------------------------
# G6.2 — workspace 大文件 hash 稳定 + 摘要截断 + byte_size 准确
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_g6_2_large_file_hash_stable_and_summary_truncated(
    ws_store: WorkspaceMemoryStore,
) -> None:
    """大文件：content_hash 稳定（同内容同 hash）、byte_size 准确、摘要截断。"""
    big = "x" * 500_000  # 500KB 单行
    content = f"first line of big file\n{big}"
    await ws_store.record_action(
        session_id="s1", path="big.txt", action="write", content=content,
    )
    row = await ws_store.get(session_id="s1", path="big.txt")
    assert row is not None
    # hash 稳定：等于独立计算的 sha1
    assert row["content_hash"] == _content_hash(content)
    # byte_size 准确（= len(content)）
    assert row["byte_size"] == len(content)
    # 摘要截断（_summarize_for_recall max_chars=120）→ summary 不应是整个 500KB
    summary = row["content_summary"] or ""
    assert len(summary) <= 120, f"摘要应截断到 ≤120 字，实际 {len(summary)}"


@pytest.mark.asyncio
async def test_g6_2b_same_content_same_hash_diff_content_diff_hash(
    ws_store: WorkspaceMemoryStore,
) -> None:
    """hash 碰撞边界：同内容同 hash，不同内容不同 hash。"""
    await ws_store.record_action(
        session_id="s1", path="a.py", action="write", content="def f(): pass",
    )
    await ws_store.record_action(
        session_id="s1", path="b.py", action="write", content="def f(): pass",
    )
    await ws_store.record_action(
        session_id="s1", path="c.py", action="write", content="def g(): pass",
    )
    a = await ws_store.get(session_id="s1", path="a.py")
    b = await ws_store.get(session_id="s1", path="b.py")
    c = await ws_store.get(session_id="s1", path="c.py")
    assert a["content_hash"] == b["content_hash"], "同内容应同 hash"
    assert a["content_hash"] != c["content_hash"], "不同内容应不同 hash"


# ----------------------------------------------------------------------
# G6.3 — 并发写入同 (session,path) 不丢失、不主键冲突
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_g6_3_concurrent_writes_no_loss_no_pk_error(
    ws_store: WorkspaceMemoryStore,
) -> None:
    """并发 record_action 同 (session,path)：UPSERT 语义，最终一条、不报错。"""
    async def write(i: int) -> None:
        await ws_store.record_action(
            session_id="s1", path="shared.py", action="write",
            content=f"version {i}",
        )

    # 20 个并发写同一 (session,path)
    await asyncio.gather(*[write(i) for i in range(20)])

    # PRIMARY KEY (session_id, path) → 最终只有一行，不报主键冲突
    row = await ws_store.get(session_id="s1", path="shared.py")
    assert row is not None
    assert row["last_action"] == "write"
    # 内容是某次写入的（UPSERT 覆盖，不重复行）
    rows = await ws_store.list_session("s1", limit=50)
    shared = [r for r in rows if r["path"] == "shared.py"]
    assert len(shared) == 1, f"并发写同 path 应只剩 1 行（UPSERT）: {len(shared)}"


@pytest.mark.asyncio
async def test_g6_3b_concurrent_facts_upsert_distinct_keys(
    tmp_path: Path,
) -> None:
    """并发 facts upsert 不同 key：全部写入、无丢失。"""
    fs = FactsStore(tmp_path / "f.db", embedder=None)

    async def up(i: int) -> None:
        await fs.upsert(
            category="preference", subject="user", key=f"k{i}",
            value=f"value {i}", confidence=0.8,
            source_msg_id=i, evidence="x",
        )

    await asyncio.gather(*[up(i) for i in range(15)])
    # 15 个不同 key 全部应写入
    hits = await fs.search("value", limit=50)
    assert len(hits) >= 15, f"15 个并发 distinct-key upsert 应全部写入: {len(hits)}"
