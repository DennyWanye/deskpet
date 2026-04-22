"""S14 — memory management API: list / delete / clear / export.

Two layers:
1. Unit tests against SqliteConversationMemory + RedactingMemoryStore for
   the new admin methods (``list_turns``, ``delete_turn``, ``list_sessions``,
   ``clear_all``).
2. WebSocket integration tests that drive the four control-WS verbs end
   to end through main.app, asserting response shape + persistence.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from memory.conversation import SqliteConversationMemory
from memory.sensitive_filter import RedactingMemoryStore


# --------------------------------------------------------------------------
# Unit: storage layer
# --------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "mem.db"


@pytest.mark.asyncio
async def test_list_turns_returns_ids_in_chronological_order(tmp_db: Path):
    store = SqliteConversationMemory(tmp_db)
    for i in range(3):
        await store.append("s1", "user", f"m{i}")
        await asyncio.sleep(0.001)  # force distinct timestamps

    turns = await store.list_turns("s1")
    assert [t.content for t in turns] == ["m0", "m1", "m2"]
    # IDs are DB primary keys — strictly monotonic on insert.
    assert [t.id for t in turns] == sorted([t.id for t in turns])
    # Every row carries the session it came from.
    assert {t.session_id for t in turns} == {"s1"}


@pytest.mark.asyncio
async def test_list_turns_across_all_sessions(tmp_db: Path):
    store = SqliteConversationMemory(tmp_db)
    await store.append("a", "user", "a1")
    await store.append("b", "user", "b1")

    everything = await store.list_turns(None)
    assert {t.session_id for t in everything} == {"a", "b"}


@pytest.mark.asyncio
async def test_delete_turn_removes_single_row(tmp_db: Path):
    store = SqliteConversationMemory(tmp_db)
    await store.append("s1", "user", "keep-1")
    await store.append("s1", "user", "remove-me")
    await store.append("s1", "user", "keep-2")

    turns = await store.list_turns("s1")
    target = next(t for t in turns if t.content == "remove-me")

    assert await store.delete_turn(target.id) is True
    remaining = await store.list_turns("s1")
    assert [t.content for t in remaining] == ["keep-1", "keep-2"]

    # Second delete on the same id is a no-op.
    assert await store.delete_turn(target.id) is False


@pytest.mark.asyncio
async def test_list_sessions_summary(tmp_db: Path):
    store = SqliteConversationMemory(tmp_db)
    await store.append("s1", "user", "a")
    await store.append("s1", "assistant", "b")
    await asyncio.sleep(0.001)
    await store.append("s2", "user", "c")

    sessions = await store.list_sessions()
    as_dict = {s.session_id: s for s in sessions}
    assert as_dict["s1"].turn_count == 2
    assert as_dict["s2"].turn_count == 1
    # Last-touched ordering: s2 came after s1.
    assert sessions[0].session_id == "s2"


@pytest.mark.asyncio
async def test_clear_all_nukes_everything(tmp_db: Path):
    store = SqliteConversationMemory(tmp_db)
    await store.append("s1", "user", "x")
    await store.append("s2", "user", "y")

    removed = await store.clear_all()
    assert removed == 2
    assert await store.list_sessions() == []
    assert await store.list_turns(None) == []


@pytest.mark.asyncio
async def test_redacting_store_passes_admin_calls_through(tmp_db: Path):
    """RedactingMemoryStore is what main.py actually holds — verify the
    admin methods reach the underlying SQLite layer unchanged."""
    inner = SqliteConversationMemory(tmp_db)
    store = RedactingMemoryStore(inner)

    await store.append("s1", "user", "hello")
    turns = await store.list_turns("s1")
    assert len(turns) == 1
    # Delete via the wrapper, confirm inner actually lost the row.
    assert await store.delete_turn(turns[0].id) is True
    assert await inner.list_turns("s1") == []


# --------------------------------------------------------------------------
# Integration: control-WS verbs
# --------------------------------------------------------------------------


def _fresh_memory_store(tmp_path: Path):
    """Swap in an empty SQLite store for the duration of one test, returning
    (store, restore_fn) so the harness can roll back afterwards.
    """
    from main import service_context

    previous = service_context.get("memory_store")
    inner = SqliteConversationMemory(tmp_path / "mem.db")
    wrapped = RedactingMemoryStore(inner)
    service_context.register("memory_store", wrapped)

    def restore():
        service_context.register("memory_store", previous)

    return wrapped, restore


def test_memory_list_roundtrip_over_ws(tmp_path: Path):
    from main import app, SHARED_SECRET

    store, restore = _fresh_memory_store(tmp_path)
    try:
        asyncio.run(store.append("s_ws", "user", "hello"))
        asyncio.run(store.append("s_ws", "assistant", "hi"))

        client = TestClient(app)
        with client.websocket_connect(
            f"/ws/control?secret={SHARED_SECRET}&session_id=s_ws"
        ) as ws:
            ws.receive_json()  # P3-S2: drain startup_status
            ws.send_json({"type": "memory_list", "payload": {}})
            msg = ws.receive_json()
        assert msg["type"] == "memory_list_response"
        contents = [t["content"] for t in msg["payload"]["turns"]]
        assert contents == ["hello", "hi"]
        # Row ids surfaced so the UI can wire delete buttons.
        assert all(isinstance(t["id"], int) for t in msg["payload"]["turns"])
    finally:
        restore()


def test_memory_delete_over_ws(tmp_path: Path):
    from main import app, SHARED_SECRET

    store, restore = _fresh_memory_store(tmp_path)
    try:
        asyncio.run(store.append("s_del", "user", "keep"))
        asyncio.run(store.append("s_del", "user", "drop"))

        # Look up the id to drop.
        turns = asyncio.run(store.list_turns("s_del"))
        victim = next(t for t in turns if t.content == "drop")

        client = TestClient(app)
        with client.websocket_connect(
            f"/ws/control?secret={SHARED_SECRET}&session_id=s_del"
        ) as ws:
            ws.receive_json()  # P3-S2: drain startup_status
            ws.send_json({"type": "memory_delete", "payload": {"id": victim.id}})
            msg = ws.receive_json()
        assert msg["type"] == "memory_delete_ack"
        assert msg["payload"] == {"id": victim.id, "deleted": True}

        # Row is actually gone on disk.
        remaining = asyncio.run(store.list_turns("s_del"))
        assert [t.content for t in remaining] == ["keep"]
    finally:
        restore()


def test_memory_delete_rejects_missing_id(tmp_path: Path):
    from main import app, SHARED_SECRET

    _store, restore = _fresh_memory_store(tmp_path)
    try:
        client = TestClient(app)
        with client.websocket_connect(
            f"/ws/control?secret={SHARED_SECRET}&session_id=s_err"
        ) as ws:
            ws.receive_json()  # P3-S2: drain startup_status
            ws.send_json({"type": "memory_delete", "payload": {}})
            msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "integer id" in msg["payload"]["message"]
    finally:
        restore()


def test_memory_clear_session_scope(tmp_path: Path):
    from main import app, SHARED_SECRET

    store, restore = _fresh_memory_store(tmp_path)
    try:
        asyncio.run(store.append("keepme", "user", "safe"))
        asyncio.run(store.append("wipe", "user", "bye"))

        client = TestClient(app)
        with client.websocket_connect(
            f"/ws/control?secret={SHARED_SECRET}&session_id=wipe"
        ) as ws:
            ws.receive_json()  # P3-S2: drain startup_status
            ws.send_json({"type": "memory_clear", "payload": {}})
            msg = ws.receive_json()
        assert msg["type"] == "memory_clear_ack"
        assert msg["payload"]["scope"] == "session"

        assert asyncio.run(store.list_turns("wipe")) == []
        # Other session untouched.
        assert len(asyncio.run(store.list_turns("keepme"))) == 1
    finally:
        restore()


def test_memory_clear_all_scope(tmp_path: Path):
    from main import app, SHARED_SECRET

    store, restore = _fresh_memory_store(tmp_path)
    try:
        asyncio.run(store.append("s1", "user", "a"))
        asyncio.run(store.append("s2", "user", "b"))

        client = TestClient(app)
        with client.websocket_connect(
            f"/ws/control?secret={SHARED_SECRET}&session_id=whatever"
        ) as ws:
            ws.receive_json()  # P3-S2: drain startup_status
            ws.send_json({"type": "memory_clear", "payload": {"scope": "all"}})
            msg = ws.receive_json()
        assert msg["type"] == "memory_clear_ack"
        assert msg["payload"]["scope"] == "all"
        assert msg["payload"]["removed"] == 2

        assert asyncio.run(store.list_sessions()) == []
    finally:
        restore()


def test_memory_export_returns_all_sessions_and_turns(tmp_path: Path):
    from main import app, SHARED_SECRET

    store, restore = _fresh_memory_store(tmp_path)
    try:
        asyncio.run(store.append("s1", "user", "one"))
        asyncio.run(store.append("s2", "user", "two"))

        client = TestClient(app)
        with client.websocket_connect(
            f"/ws/control?secret={SHARED_SECRET}&session_id=s1"
        ) as ws:
            ws.receive_json()  # P3-S2: drain startup_status
            ws.send_json({"type": "memory_export", "payload": {}})
            msg = ws.receive_json()
        assert msg["type"] == "memory_export_response"
        sessions = {s["session_id"] for s in msg["payload"]["sessions"]}
        assert sessions == {"s1", "s2"}
        turn_contents = {t["content"] for t in msg["payload"]["turns"]}
        assert turn_contents == {"one", "two"}
        assert isinstance(msg["payload"]["exported_at"], float)
    finally:
        restore()
