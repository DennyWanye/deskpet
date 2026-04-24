"""Tests for MemoryManager — P4-S4.

Covers the frozen-snapshot pattern, graceful degradation when layers
fail or are absent, and write-dispatch routing. The L3 retriever is
``None`` in S4 — post-merge Lead wires real retriever + integration
test later.

Duck-typing note: the manager prefers S1's ``append_message`` /
``get_messages`` names, but falls back to ``write_message`` /
``recent_messages`` / ``append`` to stay compatible with fakes, legacy
P3 stores, and future adapters. The ``FakeSessionDB`` below uses the
``write_message`` / ``recent_messages`` pair specifically to exercise
that fallback path.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest

from deskpet.memory.file_memory import FileMemory
from deskpet.memory.manager import MemoryManager


# ---------------------------------------------------------------------------
# Fakes — hand-rolled to keep the tests independent of S1's still-in-flight DB.
# ---------------------------------------------------------------------------
class FakeSessionDB:
    """Minimal session-DB double exercising the legacy ``write_message``
    / ``recent_messages`` fallback path in the manager's duck-typing.

    Records writes in an in-memory list; ``recent_messages`` returns them
    newest-last.
    """

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.fail_read: bool = False

    async def write_message(
        self, session_id: str, role: str, content: str, **kw
    ) -> None:
        self.messages.append(
            {"session_id": session_id, "role": role, "content": content}
        )

    async def recent_messages(
        self, session_id: Optional[str], limit: int
    ) -> list[dict]:
        if self.fail_read:
            raise RuntimeError("simulated L2 outage")
        rows = [m for m in self.messages if session_id is None or m["session_id"] == session_id]
        return rows[-limit:]


class FakeRetriever:
    """Minimal L3 retriever double — just enough for the skeleton tests."""

    def __init__(self, hits: Optional[list[dict]] = None) -> None:
        self._hits = hits or []
        self.fail: bool = False

    async def recall(self, query: str, policy: dict) -> list[dict]:
        if self.fail:
            raise RuntimeError("simulated L3 outage")
        return self._hits


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def base_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def file_memory(base_dir: Path) -> FileMemory:
    return FileMemory(base_dir=base_dir)


@pytest.fixture
def session_db() -> FakeSessionDB:
    return FakeSessionDB()


@pytest.fixture
def manager(file_memory: FileMemory, session_db: FakeSessionDB) -> MemoryManager:
    return MemoryManager(
        file_memory=file_memory,
        session_db=session_db,
        retriever=None,  # S4 skeleton — L3 wired later
    )


# ---------------------------------------------------------------------------
# initialize()
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_initialize_creates_empty_files(tmp_path: Path):
    """Fresh tmp dir → initialize() → MEMORY.md and USER.md exist as empty."""
    base_dir = tmp_path / "fresh"
    fm = FileMemory(base_dir=base_dir)
    mgr = MemoryManager(file_memory=fm, session_db=FakeSessionDB())

    assert not base_dir.exists()
    await mgr.initialize()

    assert (base_dir / "MEMORY.md").exists()
    assert (base_dir / "USER.md").exists()
    assert (base_dir / "MEMORY.md").read_text(encoding="utf-8") == ""
    assert (base_dir / "USER.md").read_text(encoding="utf-8") == ""


@pytest.mark.asyncio
async def test_initialize_is_idempotent_and_preserves_content(tmp_path: Path):
    base_dir = tmp_path / "fresh"
    base_dir.mkdir()
    (base_dir / "MEMORY.md").write_text("pre-existing", encoding="utf-8")

    fm = FileMemory(base_dir=base_dir)
    mgr = MemoryManager(file_memory=fm, session_db=FakeSessionDB())
    await mgr.initialize()
    await mgr.initialize()  # second call must not wipe

    assert (base_dir / "MEMORY.md").read_text(encoding="utf-8") == "pre-existing"


# ---------------------------------------------------------------------------
# recall() with retriever=None
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_recall_with_no_retriever_returns_l1_l2_only(manager: MemoryManager, session_db: FakeSessionDB, base_dir: Path):
    """Spec: MemoryManager.recall with retriever=None — returns
    ``{"l1": {...}, "l2": [...], "l3": []}`` without raising."""
    await manager.initialize()
    await manager.write("user is a programmer", target="user")
    await manager.write("hello", target="session", session_id="s1", role="user")
    await manager.write("hi", target="session", session_id="s1", role="assistant")

    result = await manager.recall(
        "anything",
        policy={"l1": "snapshot", "l2_top_k": 5, "l3_top_k": 5, "session_id": "s1"},
    )

    assert result["l1"] is not None
    assert "user is a programmer" in result["l1"]["user"]
    assert result["l1"]["memory"] == ""
    assert len(result["l2"]) == 2
    assert result["l2"][0]["content"] == "hello"
    assert result["l3"] == []


@pytest.mark.asyncio
async def test_recall_empty_policy_returns_default_shape(manager: MemoryManager):
    """Empty policy → l1 None, l2 uses default top_k=10, l3 empty."""
    result = await manager.recall("q")
    assert result == {"l1": None, "l2": [], "l3": []}


# ---------------------------------------------------------------------------
# recall graceful degradation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_recall_l2_failure_keeps_l1(manager: MemoryManager, session_db: FakeSessionDB, base_dir: Path):
    """Spec: MemoryManager.recall L2 failure — patch session_db to raise,
    recall still returns L1 snapshot with l2=[]."""
    await manager.initialize()
    await manager.write("stable trait", target="user")

    session_db.fail_read = True
    result = await manager.recall(
        "q",
        policy={"l1": "snapshot", "l2_top_k": 5, "session_id": "s1"},
    )

    assert result["l1"]["user"].strip() == "stable trait"
    assert result["l2"] == []
    assert result["l3"] == []


@pytest.mark.asyncio
async def test_recall_l3_failure_keeps_l1_l2(file_memory: FileMemory, session_db: FakeSessionDB):
    """When the retriever raises, recall still returns L1 + L2."""
    retriever = FakeRetriever()
    retriever.fail = True
    mgr = MemoryManager(file_memory=file_memory, session_db=session_db, retriever=retriever)
    await mgr.initialize()
    await file_memory.append("memory", "pet remembers thing")
    await session_db.write_message(session_id="s1", role="user", content="hi")

    result = await mgr.recall(
        "q",
        policy={"l1": "snapshot", "l2_top_k": 5, "l3_top_k": 5, "session_id": "s1"},
    )

    assert "pet remembers thing" in result["l1"]["memory"]
    assert len(result["l2"]) == 1
    assert result["l3"] == []


@pytest.mark.asyncio
async def test_recall_with_functional_retriever_returns_hits(file_memory: FileMemory, session_db: FakeSessionDB):
    """If a retriever is provided it flows through recall()."""
    hits = [{"message_id": 1, "score": 0.9}, {"message_id": 2, "score": 0.5}]
    mgr = MemoryManager(
        file_memory=file_memory,
        session_db=session_db,
        retriever=FakeRetriever(hits=hits),
    )
    await mgr.initialize()
    result = await mgr.recall("q", policy={"l1": "snapshot", "l3_top_k": 2})
    assert result["l3"] == hits


# ---------------------------------------------------------------------------
# Frozen-snapshot pattern
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_frozen_snapshot_pattern_mid_session_write_hits_disk(manager: MemoryManager, file_memory: FileMemory, base_dir: Path):
    """Spec: Frozen snapshot pattern —

    1) Session boot: read_snapshot → cache system prompt.
    2) Mid-session: append("memory", ...).
    3) Cached snapshot is UNCHANGED (caller owns that caching).
    4) Disk file has the new entry immediately.
    5) A *second* read_snapshot (simulating the next session boot) sees
       the new entry.
    """
    await manager.initialize()
    await file_memory.append("memory", "early lesson")

    # 1) Boot-time snapshot pinned into the system prompt.
    boot_snapshot = await file_memory.read_snapshot()
    assert "early lesson" in boot_snapshot["memory"]

    # 2) Mid-session append.
    await manager.write("fresh observation", target="memory")

    # 3) The pinned snapshot object is NOT mutated (str is immutable; the
    #    dict caller captured is its own reference).
    assert "fresh observation" not in boot_snapshot["memory"]

    # 4) On-disk file contains the new entry immediately.
    disk_text = (base_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "fresh observation" in disk_text
    assert "early lesson" in disk_text

    # 5) Next boot: new snapshot sees the new entry.
    next_boot_snapshot = await file_memory.read_snapshot()
    assert "fresh observation" in next_boot_snapshot["memory"]
    assert "early lesson" in next_boot_snapshot["memory"]


# ---------------------------------------------------------------------------
# write() dispatch
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_write_session_dispatches_to_session_db(manager: MemoryManager, session_db: FakeSessionDB):
    """Spec: MemoryManager.write session — dispatches to session_db.
    FakeSessionDB exposes ``write_message`` (not ``append_message``), so
    this locks down the second tier of the duck-typing fallback chain."""
    await manager.write(
        "hello from user",
        target="session",
        session_id="s-123",
        role="user",
    )
    assert len(session_db.messages) == 1
    assert session_db.messages[0] == {
        "session_id": "s-123",
        "role": "user",
        "content": "hello from user",
    }


@pytest.mark.asyncio
async def test_write_session_prefers_append_message_when_available(file_memory: FileMemory):
    """If the DB exposes S1's ``append_message`` name, the manager MUST
    dispatch to that in preference to older names."""

    class S1StyleDB:
        def __init__(self) -> None:
            self.appends: list[tuple] = []

        async def append_message(self, session_id: str, role: str, content: str, **kw) -> None:
            self.appends.append((session_id, role, content))

    db = S1StyleDB()
    mgr = MemoryManager(file_memory=file_memory, session_db=db)
    await mgr.write("hi", target="session", session_id="s1", role="user")
    assert db.appends == [("s1", "user", "hi")]


@pytest.mark.asyncio
async def test_write_session_requires_session_id(manager: MemoryManager):
    with pytest.raises(ValueError):
        await manager.write("oops", target="session")


@pytest.mark.asyncio
async def test_write_memory_goes_to_file_memory(manager: MemoryManager, base_dir: Path):
    await manager.write("observation", target="memory", salience=0.8)
    content = (base_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "observation" in content
    assert "{{salience=0.8}}" in content


@pytest.mark.asyncio
async def test_write_user_goes_to_user_md(manager: MemoryManager, base_dir: Path):
    await manager.write("is a cat person", target="user")
    content = (base_dir / "USER.md").read_text(encoding="utf-8")
    assert "is a cat person" in content


@pytest.mark.asyncio
async def test_write_invalid_target_raises(manager: MemoryManager):
    with pytest.raises(ValueError):
        await manager.write("x", target="bogus")


@pytest.mark.asyncio
async def test_write_session_falls_back_to_legacy_append(file_memory: FileMemory):
    """P3 SqliteConversationMemory exposes ``append`` not ``write_message``
    nor ``append_message``; the manager must still dispatch correctly
    during the transition (third tier of the fallback chain)."""

    class LegacyDB:
        def __init__(self) -> None:
            self.rows: list[tuple] = []

        async def append(self, session_id: str, role: str, content: str) -> None:
            self.rows.append((session_id, role, content))

    legacy = LegacyDB()
    mgr = MemoryManager(file_memory=file_memory, session_db=legacy)
    await mgr.write("hi", target="session", session_id="s", role="user")
    assert legacy.rows == [("s", "user", "hi")]


# ---------------------------------------------------------------------------
# recall() must never raise
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_recall_with_broken_session_db_does_not_raise(file_memory: FileMemory):
    class BrokenDB:
        async def recent_messages(self, session_id, limit):
            raise RuntimeError("kaboom")

    mgr = MemoryManager(file_memory=file_memory, session_db=BrokenDB())
    result = await mgr.recall(
        "q",
        policy={"l1": "snapshot", "l2_top_k": 3, "session_id": "s"},
    )
    assert result["l2"] == []
    assert result["l1"] == {"memory": "", "user": ""}
