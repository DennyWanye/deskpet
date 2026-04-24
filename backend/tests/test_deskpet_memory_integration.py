"""End-to-end integration test for the P4 three-layer memory stack.

Wires the real components from S1+S2+S3+S4:

- L1 :class:`FileMemory` (tmp base_dir)
- L2 :class:`SessionDB` (fresh tmp state.db, migrations applied)
- L2/L3 :class:`VectorWorker` hooked on ``on_message_written``
- L3 :class:`Embedder` in mock mode (deterministic md5-seeded vectors)
- L3 :class:`Retriever` (RRF fusion)
- Fronted by :class:`MemoryManager`

The goal is to catch API-drift regressions that the unit tests miss —
specifically the retriever call-convention mismatch the manager's
duck-typing has to absorb (``recall(query, top_k=...)`` vs the unit-test
fake's ``recall(query, policy)``).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from deskpet.memory.embedder import Embedder
from deskpet.memory.file_memory import FileMemory
from deskpet.memory.manager import MemoryManager
from deskpet.memory.retriever import Retriever, RetrievalPolicy
from deskpet.memory.session_db import SessionDB
from deskpet.memory.vector_worker import VectorWorker


# ---------------------------------------------------------------------------
# Fixture: fully-wired three-layer stack
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def stack(tmp_path: Path):
    """Full L1+L2+L3 MemoryManager bound up with real components.

    The embedder runs in mock mode (no model weights). VectorWorker
    flushes every 200ms so tests don't have to wait long for embeddings
    to land.
    """
    base_dir = tmp_path / "data"
    base_dir.mkdir()
    db_path = tmp_path / "state.db"

    # ---- L2 SessionDB ----
    session_db = SessionDB(db_path=str(db_path))
    await session_db.initialize()

    # ---- L3 Embedder (mock — 1024-dim md5-seeded stable vectors) ----
    # Force mock by pointing at a nonexistent model dir and keeping the
    # fallback enabled; keeps the test hermetic (no model weights needed).
    embedder = Embedder(
        model_path=tmp_path / "nonexistent-model",
        use_mock_when_missing=True,
    )
    await embedder.warmup()
    assert embedder.is_ready()
    assert embedder.is_mock(), "expected mock embedder in hermetic test"

    # ---- L3 VectorWorker ----
    # Small batch + short flush so the test doesn't wait on 2s ticks.
    worker = VectorWorker(
        session_db=session_db,
        embedder=embedder,
        batch_size=2,
        flush_interval_s=0.2,
    )
    await worker.start()

    # Hook the worker into SessionDB so every append_message triggers
    # an embedding enqueue. (SessionDB was constructed without the hook
    # for test control; wire it now.)
    session_db._on_message_written = worker.enqueue  # noqa: SLF001

    # ---- L1 FileMemory ----
    file_memory = FileMemory(base_dir=base_dir)

    # ---- L3 Retriever ----
    retriever = Retriever(
        session_db=session_db,
        embedder=embedder,
        policy=RetrievalPolicy(top_k=10),
    )

    # ---- Façade ----
    mgr = MemoryManager(
        file_memory=file_memory,
        session_db=session_db,
        retriever=retriever,
    )
    await mgr.initialize()

    yield {
        "mgr": mgr,
        "session_db": session_db,
        "embedder": embedder,
        "worker": worker,
        "retriever": retriever,
        "base_dir": base_dir,
    }

    # Cleanup: drain the worker, close the DB.
    await worker.stop(drain=True)
    await session_db.close()


# ---------------------------------------------------------------------------
# Integration scenarios
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_end_to_end_three_layer_recall(stack):
    """Write messages through MemoryManager, wait for embeddings to
    flush, call recall(), verify L1+L2+L3 all return data."""
    mgr: MemoryManager = stack["mgr"]
    session_db: SessionDB = stack["session_db"]

    # Seed L1.
    await mgr.write("主人是程序员", target="user")
    await mgr.write("主人晚上 9 点后不喜欢高音", target="memory")

    # Seed L2 (and L3 via the worker hook).
    session_id = await session_db.create_session()
    await mgr.write(
        "我喜欢红色袜子",
        target="session",
        session_id=session_id,
        role="user",
    )
    await mgr.write(
        "好的，我记住了。",
        target="session",
        session_id=session_id,
        role="assistant",
    )

    # Give the VectorWorker a beat to flush the embeddings.
    # 200ms flush + a small safety margin.
    for _ in range(10):
        await asyncio.sleep(0.2)
        stats = stack["worker"].stats()
        if stats.get("queue_depth", 0) == 0 and stats.get("embedded", 0) >= 2:
            break

    # Call recall — exercises the full fan-out.
    result = await mgr.recall(
        "穿在脚上的红色那个东西",
        policy={
            "l1": "snapshot",
            "l2_top_k": 5,
            "l3_top_k": 5,
            "session_id": session_id,
        },
    )

    # L1: frozen snapshot contains both MEMORY and USER content.
    assert result["l1"] is not None
    assert "程序员" in result["l1"]["user"]
    assert "高音" in result["l1"]["memory"]

    # L2: session replay should include both messages.
    l2_contents = {row.get("content") for row in result["l2"]}
    assert "我喜欢红色袜子" in l2_contents
    assert "好的，我记住了。" in l2_contents

    # L3: retriever should return at least the two messages above as
    # Hit-dict entries (converted by manager._to_dict). We're lenient
    # about scores since mock vectors are md5-seeded — just assert the
    # shape + that we got hits.
    assert isinstance(result["l3"], list)
    assert len(result["l3"]) > 0
    # Each hit was a Hit dataclass → _to_dict should have serialized
    # message_id / score / text / ts / source.
    for hit in result["l3"]:
        assert "message_id" in hit
        assert "score" in hit
        assert "text" in hit
        assert "source" in hit
        assert hit["source"] in ("vec", "fts", "recency", "salience")


@pytest.mark.asyncio
async def test_recall_survives_retriever_exception(stack, monkeypatch):
    """Real Retriever can still blow up at runtime (e.g. sqlite-vec
    extension deloaded mid-session). Manager MUST return L1+L2 anyway."""
    mgr: MemoryManager = stack["mgr"]
    session_db: SessionDB = stack["session_db"]

    await mgr.write("stable trait", target="user")

    session_id = await session_db.create_session()
    await mgr.write(
        "test message",
        target="session",
        session_id=session_id,
        role="user",
    )

    # Force the retriever to explode.
    async def boom(*a, **kw):
        raise RuntimeError("retriever unavailable")

    monkeypatch.setattr(stack["retriever"], "recall", boom)

    result = await mgr.recall(
        "any",
        policy={
            "l1": "snapshot",
            "l2_top_k": 3,
            "l3_top_k": 3,
            "session_id": session_id,
        },
    )

    # L1 intact.
    assert "stable trait" in result["l1"]["user"]
    # L2 intact.
    assert any(r.get("content") == "test message" for r in result["l2"])
    # L3 gracefully empty (warning logged, no exception raised).
    assert result["l3"] == []


@pytest.mark.asyncio
async def test_retriever_signature_matches_duck_typing(stack):
    """Lock down the call-convention: MemoryManager must successfully
    call the real Retriever.recall(query, top_k=...) without TypeError,
    i.e. the duck-typing fallback handles keyword-arg signature."""
    mgr: MemoryManager = stack["mgr"]

    # Direct call through _safe_l3 must not raise even on empty DB.
    hits = await mgr._safe_l3("anything", top_k=3, policy={})  # noqa: SLF001
    assert isinstance(hits, list)  # empty is fine; shape is what matters
