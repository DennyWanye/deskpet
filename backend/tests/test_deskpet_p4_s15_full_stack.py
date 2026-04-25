"""P4-S15 — full-stack integration tests.

Replicates main.py's S15 wire-in (Embedder + SessionDB + VectorWorker +
Retriever + MemoryManager + dual-write adapter + ContextAssembler with
embedder-aware classifier + MCPManager) against a tmp dir, and validates
that:

1. Embedder warms up in mock mode (no BGE-M3 weights present in test env).
2. SessionDB initialises and the on_message_written hook fires for new turns.
3. The dual-write adapter mirrors writes to both legacy memory_store
   AND SessionDB.
4. Retriever can recall (returns empty when L3 has no fts/vec content yet —
   we just verify it doesn't crash).
5. ContextAssembler with embedder reports ``classifier_path`` reachable
   via the IPC layer (``rule`` / ``embed`` / ``llm`` / ``default`` —
   anything except None).
6. MCPManager bootstraps cleanly with empty/default config.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from context import ServiceContext
from deskpet.agent.assembler import build_default_assembler
from deskpet.memory.embedder import Embedder
from deskpet.memory.file_memory import FileMemory
from deskpet.memory.manager import MemoryManager
from deskpet.memory.retriever import Retriever
from deskpet.memory.session_db import SessionDB
from deskpet.memory.vector_worker import VectorWorker
from deskpet.mcp.bootstrap import create_and_start_from_config
from deskpet.skills.loader import SkillLoader
from memory.conversation import SqliteConversationMemory


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, obj: dict) -> None:
        self.sent.append(obj)


@pytest_asyncio.fixture
async def full_stack(tmp_path: Path) -> dict:
    """Mirror main.py's full S15 wire-in but in a tmp dir + with mock embedder."""
    # Legacy L2
    legacy_db = tmp_path / "memory.db"
    legacy_store = SqliteConversationMemory(db_path=str(legacy_db))

    # P4-S15: SessionDB at side-by-side state.db
    state_db = tmp_path / "state.db"
    session_db = SessionDB(db_path=state_db)
    await session_db.initialize()

    # Embedder forced into mock mode (model dir does not exist).
    embedder = Embedder(
        model_path=tmp_path / "no-bge",  # always missing → mock fallback
        use_mock_when_missing=True,
    )
    await embedder.warmup()

    # VectorWorker
    worker = VectorWorker(session_db=session_db, embedder=embedder)
    await worker.start()
    # Wire enqueue hook AFTER worker started (mirrors main.py).
    session_db._on_message_written = worker.enqueue  # type: ignore[attr-defined]

    # Retriever
    retriever = Retriever(session_db=session_db, embedder=embedder)

    # L1
    fm = FileMemory(base_dir=tmp_path)
    fm.ensure_base_dir()

    # MemoryManager — SessionDB as L2, retriever live
    mm = MemoryManager(file_memory=fm, session_db=session_db, retriever=retriever)
    await mm.initialize()

    # Dual-write adapter (matches main.py's _DualWriteMemoryStore)
    class DualWrite:
        def __init__(self, primary, sdb):
            self._primary = primary
            self._sdb = sdb

        async def get_recent(self, session_id, limit=10):
            return await self._primary.get_recent(session_id, limit)

        async def append(self, session_id, role, content):
            await self._primary.append(session_id, role, content)
            await self._sdb.append_message(
                session_id=session_id, role=role, content=content
            )

    dual_store = DualWrite(legacy_store, session_db)

    # SkillLoader
    import deskpet.skills.builtin as _builtin_pkg
    builtin_dir = Path(_builtin_pkg.__file__).parent
    user_dir = tmp_path / "skills-user"
    user_dir.mkdir(parents=True, exist_ok=True)
    loader = SkillLoader(skill_dirs=[builtin_dir, user_dir], enable_watch=False)
    await loader.start()

    # Assembler with embedder
    assembler = build_default_assembler(
        embedder=embedder,
        llm_registry=None,
        enabled=True,
        context_window=32_000,
        budget_ratio=0.6,
    )

    sc = ServiceContext()
    sc.register("memory_store", dual_store)
    sc.register("file_memory", fm)
    sc.register("memory_manager", mm)
    sc.register("skill_loader", loader)
    sc.register("context_assembler", assembler)

    # MCPManager — empty config means no servers; just verify it starts.
    mcp = await create_and_start_from_config({}, tool_registry=None)
    sc.register("mcp_manager", mcp)

    yield {
        "sc": sc,
        "embedder": embedder,
        "session_db": session_db,
        "worker": worker,
        "retriever": retriever,
        "memory_manager": mm,
        "dual_store": dual_store,
        "assembler": assembler,
        "mcp": mcp,
        "skill_loader": loader,
    }

    # Cleanup
    await mcp.stop()
    await worker.stop()
    await loader.stop()


class TestS15FullStack:
    @pytest.mark.asyncio
    async def test_embedder_warmup_falls_back_to_mock(self, full_stack: dict) -> None:
        emb: Embedder = full_stack["embedder"]
        assert emb.is_ready() is True
        assert emb.is_mock() is True

    @pytest.mark.asyncio
    async def test_dual_write_lands_in_both_stores(self, full_stack: dict) -> None:
        ds = full_stack["dual_store"]
        sdb: SessionDB = full_stack["session_db"]
        await ds.append("sess-x", "user", "hello dualwrite")
        # Primary
        recent = await ds.get_recent("sess-x", 5)
        assert any(t.content == "hello dualwrite" for t in recent)
        # Mirror
        msgs = await sdb.get_messages("sess-x", 5)
        contents = [m.get("content") if isinstance(m, dict) else getattr(m, "content", None) for m in msgs]
        assert "hello dualwrite" in contents

    @pytest.mark.asyncio
    async def test_retriever_recalls_without_crashing(self, full_stack: dict) -> None:
        retriever: Retriever = full_stack["retriever"]
        # No content seeded → just verify it returns a list, not raises.
        hits = await retriever.recall("anything", top_k=5)
        assert isinstance(hits, list)

    @pytest.mark.asyncio
    async def test_memory_manager_recall_layered(self, full_stack: dict) -> None:
        mm: MemoryManager = full_stack["memory_manager"]
        # L1 snapshot + L2/L3 should all return without raising.
        result = await mm.recall(
            "anything",
            policy={
                "l1": "snapshot",
                "l2_top_k": 5,
                "l3_top_k": 5,
                "session_id": "sess-x",
            },
        )
        assert "l1" in result and "l2" in result and "l3" in result

    @pytest.mark.asyncio
    async def test_assembler_with_embedder_runs(self, full_stack: dict) -> None:
        a = full_stack["assembler"]
        bundle = await a.assemble(
            user_message="recall what we talked about yesterday",
            memory_manager=full_stack["memory_manager"],
            skill_registry=full_stack["skill_loader"],
            session_id="sess-classify",
        )
        # classifier_path should be one of {rule, embed, llm, default} —
        # it MUST be set; absence == regression.
        assert bundle.decisions.classifier_path in {"rule", "embed", "llm", "default"}

    @pytest.mark.asyncio
    async def test_mcp_manager_starts_with_empty_config(self, full_stack: dict) -> None:
        mcp = full_stack["mcp"]
        # Empty config → no servers running, but the manager itself is alive.
        states = mcp.server_state()
        assert isinstance(states, dict)
        # No servers since config was {}
        assert len(states) == 0
