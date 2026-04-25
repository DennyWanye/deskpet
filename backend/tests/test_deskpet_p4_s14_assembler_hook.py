"""P4-S14 — ContextAssembler integration tests.

Verifies:
1. ``build_default_assembler`` actually constructs without an embedder/LLM.
2. Calling ``assemble()`` with the same args main.py passes records a decision
   that the IPC ``decisions_list`` handler can serialize to the shape the
   frontend expects (latency_ms, token_breakdown, timestamp, session_id).
3. After ``feedback(bundle, final_response=...)`` the decision shows the
   response length.
4. Frontend-friendly aliases are present alongside canonical fields.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
import pytest_asyncio

from context import ServiceContext
from deskpet.agent.assembler import build_default_assembler
from deskpet.memory.file_memory import FileMemory
from deskpet.memory.manager import MemoryManager
from deskpet.skills.loader import SkillLoader
from memory.conversation import SqliteConversationMemory
import p4_ipc


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, obj: dict) -> None:
        self.sent.append(obj)


@pytest_asyncio.fixture
async def wired_sc(tmp_path: Path) -> ServiceContext:
    """Replicate main.py's S13 + S14 wire-in against a tmp data dir."""
    sc = ServiceContext()

    # L2 store
    memory_store = SqliteConversationMemory(db_path=str(tmp_path / "memory.db"))
    sc.register("memory_store", memory_store)

    # L1 + manager + skills (S13)
    fm = FileMemory(base_dir=tmp_path)
    fm.ensure_base_dir()
    sc.register("file_memory", fm)

    mm = MemoryManager(file_memory=fm, session_db=memory_store)
    await mm.initialize()
    sc.register("memory_manager", mm)

    import deskpet.skills.builtin as _builtin_pkg
    builtin_dir = Path(_builtin_pkg.__file__).parent
    user_dir = tmp_path / "skills-user"
    user_dir.mkdir(parents=True, exist_ok=True)
    loader = SkillLoader(skill_dirs=[builtin_dir, user_dir], enable_watch=False)
    await loader.start()
    sc.register("skill_loader", loader)

    # S14 — assembler
    assembler = build_default_assembler(
        embedder=None,
        llm_registry=None,
        enabled=True,
        context_window=32_000,
        budget_ratio=0.6,
    )
    sc.register("context_assembler", assembler)

    yield sc

    # Clean up the watchdog-less loader's started state.
    try:
        await loader.stop()
    except Exception:
        pass


class TestS14AssemblerHook:
    @pytest.mark.asyncio
    async def test_assembler_runs_without_embedder_or_llm(
        self, wired_sc: ServiceContext
    ) -> None:
        """build_default_assembler with embedder=None / llm=None must not crash."""
        a = wired_sc.get("context_assembler")
        assert a is not None
        assert a.enabled is True

        bundle = await a.assemble(
            user_message="hello pet",
            memory_manager=wired_sc.get("memory_manager"),
            tool_registry=wired_sc.get("tool_router"),  # None — fine
            skill_registry=wired_sc.get("skill_loader"),
            mcp_manager=wired_sc.get("mcp_manager"),  # None — fine
            session_id="sess-1",
        )
        assert bundle is not None
        assert bundle.task_type in {"chat", "tool_use", "memory", "skill"}
        # Decisions auto-stamped:
        assert bundle.decisions.assembly_latency_ms >= 0.0

    @pytest.mark.asyncio
    async def test_decisions_round_trip_through_ipc(
        self, wired_sc: ServiceContext
    ) -> None:
        """Stamp timestamp/session_id like main.py does, then list via IPC."""
        a = wired_sc.get("context_assembler")
        bundle = await a.assemble(
            user_message="what's the time?",
            memory_manager=wired_sc.get("memory_manager"),
            skill_registry=wired_sc.get("skill_loader"),
            session_id="sess-trace",
        )
        # Mirror main.py's stamp.
        bundle.decisions.timestamp = time.time()
        bundle.decisions.session_id = "sess-trace"
        a.feedback(bundle, final_response="It's 12:34")

        ws = FakeWS()
        await p4_ipc.handle(ws, "sess-trace", "decisions_list", {"limit": 10}, wired_sc)
        resp = ws.sent[-1]
        assert resp["type"] == "decisions_list_response"
        decisions = resp["payload"]["decisions"]
        assert len(decisions) >= 1

        last = decisions[-1]
        # Frontend-friendly aliases (P4-S14 to_dict additions):
        assert "latency_ms" in last
        assert "token_breakdown" in last
        assert isinstance(last["token_breakdown"], dict)
        assert last["timestamp"] is not None
        assert last["session_id"] == "sess-trace"
        assert last["reason"].startswith(last["task_type"])
        # Canonical fields still there for backward compat:
        assert "assembly_latency_ms" in last
        assert "classifier_path" in last
        assert "components" in last
        # final_response_len was stamped by feedback():
        assert last["final_response_len"] == len("It's 12:34")
        # Should NOT carry the "context_assembler_not_registered" reason now.
        assert resp["payload"].get("reason") != "context_assembler_not_registered"

    @pytest.mark.asyncio
    async def test_bundle_build_messages_yields_chat_shape(
        self, wired_sc: ServiceContext
    ) -> None:
        """``bundle.build_messages(user_message=...)`` must produce a list of
        ``{role, content}`` dicts the existing chat_stream signature expects."""
        a = wired_sc.get("context_assembler")
        bundle = await a.assemble(
            user_message="ping",
            memory_manager=wired_sc.get("memory_manager"),
            skill_registry=wired_sc.get("skill_loader"),
            session_id="sess-shape",
        )
        msgs = bundle.build_messages(user_message="ping")
        assert isinstance(msgs, list)
        assert msgs, "bundle should at least contain the user message"
        # Last entry is always the user turn.
        assert msgs[-1]["role"] == "user"
        assert msgs[-1]["content"] == "ping"
        # Every entry has both role + content.
        for m in msgs:
            assert "role" in m and "content" in m

    @pytest.mark.asyncio
    async def test_assembler_failure_doesnt_break_legacy_path(
        self, tmp_path: Path
    ) -> None:
        """Smoke: with no assembler registered, decisions_list returns the
        graceful empty response (sanity check that S14 doesn't accidentally
        require the assembler to be present)."""
        sc = ServiceContext()
        ws = FakeWS()
        await p4_ipc.handle(ws, "s1", "decisions_list", {"limit": 5}, sc)
        resp = ws.sent[-1]
        assert resp["type"] == "decisions_list_response"
        assert resp["payload"]["decisions"] == []
        assert resp["payload"]["reason"] == "context_assembler_not_registered"
