"""Tests for the P4-S11 IPC handlers (MemoryPanel + ContextTrace).

Cover the five new message types with:

- happy path (service registered, returns shaped payload)
- degraded path (service absent → empty list + reason)
- error path (service raises → empty list + error log, no crash)
- validation path (bad payload → error frame)

Isolated from main.py — tests talk to ``p4_ipc.handle`` directly with a
``FakeWebSocket`` and an in-memory ``FakeServiceContext``.
"""
from __future__ import annotations

from typing import Any

import pytest

import p4_ipc


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, msg: dict[str, Any]) -> None:
        self.sent.append(msg)


class FakeServiceContext:
    def __init__(self, **services: Any) -> None:
        self._services = dict(services)

    def get(self, name: str) -> Any:
        return self._services.get(name)


class FakeSkillLoader:
    def __init__(self, skills: list[dict[str, Any]] | Exception) -> None:
        self._skills = skills

    def list_skills(self) -> list[dict[str, Any]]:
        if isinstance(self._skills, Exception):
            raise self._skills
        return list(self._skills)


class FakeAssembler:
    def __init__(self, decisions: list[dict[str, Any]] | Exception) -> None:
        self._decisions = decisions
        self.last_n: int | None = None

    def recent_decisions(self, n: int = 20) -> list[dict[str, Any]]:
        self.last_n = n
        if isinstance(self._decisions, Exception):
            raise self._decisions
        return list(self._decisions)


class FakeMemoryManager:
    """Emulates MemoryManager.recall() → object with .l3 attribute."""

    def __init__(self, hits: list[Any] | Exception) -> None:
        self._hits = hits
        self.last_query: str | None = None
        self.last_policy: dict[str, Any] | None = None

    async def recall(self, query: str, policy: dict[str, Any]) -> Any:
        self.last_query = query
        self.last_policy = policy
        if isinstance(self._hits, Exception):
            raise self._hits

        class _Recall:
            def __init__(self, l3: list[Any]) -> None:
                self.l3 = l3

        return _Recall(list(self._hits))


class FakeFileMemory:
    def __init__(self) -> None:
        self._entries: dict[str, list[dict[str, Any]]] = {
            "memory": [
                {"text": "Alice likes tea", "salience": 0.9},
                {"text": "Birthday 2020-01-15", "salience": 0.7},
            ],
            "user": [
                {"text": "Prefers concise replies", "salience": 0.6},
            ],
        }
        self.list_calls: list[str] = []
        self.delete_calls: list[tuple[str, int]] = []
        self._should_raise = False

    def make_raise(self) -> None:
        self._should_raise = True

    async def list_entries(self, target: str) -> list[dict[str, Any]]:
        if self._should_raise:
            raise RuntimeError("boom")
        self.list_calls.append(target)
        return list(self._entries.get(target, []))

    async def delete_entry(self, target: str, index: int) -> bool:
        self.delete_calls.append((target, index))
        entries = self._entries.get(target, [])
        if index < 0 or index >= len(entries):
            return False
        del entries[index]
        return True


# ---------------------------------------------------------------------------
# skills_list
# ---------------------------------------------------------------------------
class TestSkillsList:
    @pytest.mark.asyncio
    async def test_returns_list_when_loader_registered(self) -> None:
        ws = FakeWebSocket()
        sc = FakeServiceContext(
            skill_loader=FakeSkillLoader(
                [
                    {"name": "recall-yesterday", "scope": "built-in"},
                    {"name": "hello", "scope": "user"},
                ]
            )
        )
        await p4_ipc.handle(ws, "s1", "skills_list", {}, sc)
        assert len(ws.sent) == 1
        m = ws.sent[0]
        assert m["type"] == "skills_list_response"
        assert len(m["payload"]["skills"]) == 2
        assert m["payload"]["skills"][0]["name"] == "recall-yesterday"

    @pytest.mark.asyncio
    async def test_graceful_when_loader_absent(self) -> None:
        ws = FakeWebSocket()
        sc = FakeServiceContext()  # no skill_loader
        await p4_ipc.handle(ws, "s1", "skills_list", {}, sc)
        m = ws.sent[0]
        assert m["type"] == "skills_list_response"
        assert m["payload"]["skills"] == []
        assert m["payload"]["reason"] == "skill_loader_not_registered"

    @pytest.mark.asyncio
    async def test_loader_raise_returns_empty_not_crash(self) -> None:
        ws = FakeWebSocket()
        sc = FakeServiceContext(skill_loader=FakeSkillLoader(RuntimeError("boom")))
        await p4_ipc.handle(ws, "s1", "skills_list", {}, sc)
        m = ws.sent[0]
        assert m["type"] == "skills_list_response"
        assert m["payload"]["skills"] == []


# ---------------------------------------------------------------------------
# decisions_list
# ---------------------------------------------------------------------------
class TestDecisionsList:
    @pytest.mark.asyncio
    async def test_returns_decisions_respecting_limit(self) -> None:
        ws = FakeWebSocket()
        ass = FakeAssembler(
            [
                {"task_type": "chat", "latency_ms": 42},
                {"task_type": "recall", "latency_ms": 180},
            ]
        )
        sc = FakeServiceContext(context_assembler=ass)
        await p4_ipc.handle(
            ws, "s1", "decisions_list", {"limit": 10}, sc
        )
        assert ass.last_n == 10
        m = ws.sent[0]
        assert m["type"] == "decisions_list_response"
        assert len(m["payload"]["decisions"]) == 2

    @pytest.mark.asyncio
    async def test_limit_clamped_to_safe_range(self) -> None:
        ws = FakeWebSocket()
        ass = FakeAssembler([])
        sc = FakeServiceContext(context_assembler=ass)
        await p4_ipc.handle(ws, "s1", "decisions_list", {"limit": 999}, sc)
        assert ass.last_n == 200  # clamped
        ws.sent.clear()
        await p4_ipc.handle(ws, "s1", "decisions_list", {"limit": 0}, sc)
        assert ass.last_n == 1  # clamped
        ws.sent.clear()
        await p4_ipc.handle(ws, "s1", "decisions_list", {"limit": "bogus"}, sc)
        assert ass.last_n == 50  # default on bad input

    @pytest.mark.asyncio
    async def test_graceful_when_assembler_absent(self) -> None:
        ws = FakeWebSocket()
        sc = FakeServiceContext()
        await p4_ipc.handle(ws, "s1", "decisions_list", {}, sc)
        m = ws.sent[0]
        assert m["payload"]["decisions"] == []
        assert m["payload"]["reason"] == "context_assembler_not_registered"


# ---------------------------------------------------------------------------
# memory_search
# ---------------------------------------------------------------------------
class TestMemorySearch:
    @pytest.mark.asyncio
    async def test_routes_query_to_l3_only(self) -> None:
        ws = FakeWebSocket()
        mm = FakeMemoryManager(
            [
                {"text": "yesterday we discussed X", "score": 0.87},
                {"text": "older note", "score": 0.65},
            ]
        )
        sc = FakeServiceContext(memory_manager=mm)
        await p4_ipc.handle(
            ws, "s1", "memory_search", {"query": "yesterday", "top_k": 5}, sc
        )
        assert mm.last_query == "yesterday"
        # L1/L2 must be OFF — this endpoint is a pure vector search.
        assert mm.last_policy["l1"] == "skip"
        assert mm.last_policy["l2_top_k"] == 0
        assert mm.last_policy["l3_top_k"] == 5
        m = ws.sent[0]
        assert m["type"] == "memory_search_response"
        assert m["payload"]["query"] == "yesterday"
        assert len(m["payload"]["hits"]) == 2
        assert m["payload"]["hits"][0]["score"] == 0.87

    @pytest.mark.asyncio
    async def test_empty_query_rejected(self) -> None:
        ws = FakeWebSocket()
        sc = FakeServiceContext(memory_manager=FakeMemoryManager([]))
        await p4_ipc.handle(
            ws, "s1", "memory_search", {"query": "   "}, sc
        )
        m = ws.sent[0]
        assert m["type"] == "error"
        assert "non-empty" in m["payload"]["message"]

    @pytest.mark.asyncio
    async def test_graceful_when_manager_absent(self) -> None:
        ws = FakeWebSocket()
        sc = FakeServiceContext()
        await p4_ipc.handle(
            ws, "s1", "memory_search", {"query": "anything"}, sc
        )
        m = ws.sent[0]
        assert m["type"] == "memory_search_response"
        assert m["payload"]["hits"] == []
        assert m["payload"]["reason"] == "memory_manager_not_registered"

    @pytest.mark.asyncio
    async def test_recall_raise_returns_error_payload(self) -> None:
        ws = FakeWebSocket()
        sc = FakeServiceContext(
            memory_manager=FakeMemoryManager(RuntimeError("db died"))
        )
        await p4_ipc.handle(
            ws, "s1", "memory_search", {"query": "x"}, sc
        )
        m = ws.sent[0]
        assert m["type"] == "memory_search_response"
        assert m["payload"]["hits"] == []
        assert "db died" in m["payload"].get("error", "")

    @pytest.mark.asyncio
    async def test_top_k_clamped(self) -> None:
        ws = FakeWebSocket()
        mm = FakeMemoryManager([])
        sc = FakeServiceContext(memory_manager=mm)
        await p4_ipc.handle(
            ws, "s1", "memory_search", {"query": "x", "top_k": 999}, sc
        )
        assert mm.last_policy["l3_top_k"] == 50  # clamped
        ws.sent.clear()
        await p4_ipc.handle(
            ws, "s1", "memory_search", {"query": "x", "top_k": 0}, sc
        )
        assert mm.last_policy["l3_top_k"] == 1  # clamped


# ---------------------------------------------------------------------------
# memory_l1_list
# ---------------------------------------------------------------------------
class TestMemoryL1List:
    @pytest.mark.asyncio
    async def test_returns_indexed_entries(self) -> None:
        ws = FakeWebSocket()
        fm = FakeFileMemory()
        sc = FakeServiceContext(file_memory=fm)
        await p4_ipc.handle(ws, "s1", "memory_l1_list", {"target": "memory"}, sc)
        m = ws.sent[0]
        assert m["type"] == "memory_l1_list_response"
        assert m["payload"]["target"] == "memory"
        entries = m["payload"]["entries"]
        assert len(entries) == 2
        # Index stamp must be monotonic starting at 0 so the UI can send
        # it back as memory_l1_delete payload without ambiguity.
        assert entries[0]["index"] == 0
        assert entries[1]["index"] == 1
        assert entries[0]["text"] == "Alice likes tea"
        assert entries[0]["salience"] == 0.9

    @pytest.mark.asyncio
    async def test_default_target_is_memory(self) -> None:
        ws = FakeWebSocket()
        fm = FakeFileMemory()
        sc = FakeServiceContext(file_memory=fm)
        await p4_ipc.handle(ws, "s1", "memory_l1_list", {}, sc)
        assert fm.list_calls == ["memory"]

    @pytest.mark.asyncio
    async def test_invalid_target_rejected(self) -> None:
        ws = FakeWebSocket()
        sc = FakeServiceContext(file_memory=FakeFileMemory())
        await p4_ipc.handle(
            ws, "s1", "memory_l1_list", {"target": "nope"}, sc
        )
        m = ws.sent[0]
        assert m["type"] == "error"

    @pytest.mark.asyncio
    async def test_user_target_returns_user_entries(self) -> None:
        ws = FakeWebSocket()
        fm = FakeFileMemory()
        sc = FakeServiceContext(file_memory=fm)
        await p4_ipc.handle(ws, "s1", "memory_l1_list", {"target": "user"}, sc)
        m = ws.sent[0]
        assert len(m["payload"]["entries"]) == 1
        assert "concise" in m["payload"]["entries"][0]["text"]

    @pytest.mark.asyncio
    async def test_resolves_file_memory_via_manager(self) -> None:
        """If file_memory isn't registered directly, fall back to manager.file_memory."""
        ws = FakeWebSocket()
        fm = FakeFileMemory()

        class FakeMgrWithFM:
            def __init__(self, fm: FakeFileMemory) -> None:
                self.file_memory = fm

        sc = FakeServiceContext(memory_manager=FakeMgrWithFM(fm))
        await p4_ipc.handle(ws, "s1", "memory_l1_list", {"target": "memory"}, sc)
        m = ws.sent[0]
        assert m["type"] == "memory_l1_list_response"
        assert len(m["payload"]["entries"]) == 2

    @pytest.mark.asyncio
    async def test_graceful_when_file_memory_absent(self) -> None:
        ws = FakeWebSocket()
        sc = FakeServiceContext()
        await p4_ipc.handle(ws, "s1", "memory_l1_list", {}, sc)
        m = ws.sent[0]
        assert m["payload"]["entries"] == []
        assert m["payload"]["reason"] == "file_memory_not_registered"


# ---------------------------------------------------------------------------
# memory_l1_delete
# ---------------------------------------------------------------------------
class TestMemoryL1Delete:
    @pytest.mark.asyncio
    async def test_delete_by_index_succeeds(self) -> None:
        ws = FakeWebSocket()
        fm = FakeFileMemory()
        sc = FakeServiceContext(file_memory=fm)
        await p4_ipc.handle(
            ws, "s1", "memory_l1_delete", {"target": "memory", "index": 0}, sc
        )
        m = ws.sent[0]
        assert m["type"] == "memory_l1_delete_ack"
        assert m["payload"]["deleted"] is True
        assert m["payload"]["index"] == 0
        assert fm.delete_calls == [("memory", 0)]

    @pytest.mark.asyncio
    async def test_delete_out_of_range_returns_false(self) -> None:
        ws = FakeWebSocket()
        fm = FakeFileMemory()
        sc = FakeServiceContext(file_memory=fm)
        await p4_ipc.handle(
            ws,
            "s1",
            "memory_l1_delete",
            {"target": "memory", "index": 999},
            sc,
        )
        m = ws.sent[0]
        assert m["payload"]["deleted"] is False

    @pytest.mark.asyncio
    async def test_missing_index_rejected(self) -> None:
        ws = FakeWebSocket()
        sc = FakeServiceContext(file_memory=FakeFileMemory())
        await p4_ipc.handle(
            ws, "s1", "memory_l1_delete", {"target": "memory"}, sc
        )
        m = ws.sent[0]
        assert m["type"] == "error"
        assert "integer index" in m["payload"]["message"]

    @pytest.mark.asyncio
    async def test_graceful_when_file_memory_absent(self) -> None:
        ws = FakeWebSocket()
        sc = FakeServiceContext()
        await p4_ipc.handle(
            ws,
            "s1",
            "memory_l1_delete",
            {"target": "memory", "index": 0},
            sc,
        )
        m = ws.sent[0]
        assert m["payload"]["deleted"] is False
        assert m["payload"]["reason"] == "file_memory_not_registered"


# ---------------------------------------------------------------------------
# P4-S16: EmbedderStatus
# ---------------------------------------------------------------------------
class FakeEmbedder:
    """Mirrors deskpet.memory.embedder.Embedder 的关键 surface。"""

    def __init__(
        self,
        ready: bool = True,
        mock: bool = True,
        path: str = "/fake/bge-m3",
        raise_on_call: Exception | None = None,
    ) -> None:
        self._ready = ready
        self._mock = mock
        self._model_path = path
        self._raise = raise_on_call

    def is_ready(self) -> bool:
        if self._raise:
            raise self._raise
        return self._ready

    def is_mock(self) -> bool:
        if self._raise:
            raise self._raise
        return self._mock


class FakeServiceContextWithEmbedder(FakeServiceContext):
    """P4-S16: embedder 走正式注册槽（之前是私有 _p4_embedder 属性）。

    fixture 把 embedder 塞进 _services dict，让 _get_service(sc, "embedder")
    通过 ``sc.get("embedder")`` 命中。
    """

    def __init__(self, embedder: Any = None, **services: Any) -> None:
        if embedder is not None:
            services["embedder"] = embedder
        super().__init__(**services)


class TestEmbedderStatus:
    @pytest.mark.asyncio
    async def test_returns_status_when_real_embedder_registered(self) -> None:
        emb = FakeEmbedder(ready=True, mock=False, path="C:/models/bge-m3")
        ws = FakeWebSocket()
        sc = FakeServiceContextWithEmbedder(embedder=emb)
        await p4_ipc.handle(ws, "s1", "embedder_status", {}, sc)
        m = ws.sent[0]
        assert m["type"] == "embedder_status_response"
        assert m["payload"]["is_ready"] is True
        assert m["payload"]["is_mock"] is False
        assert m["payload"]["model_path"] == "C:/models/bge-m3"
        assert "reason" not in m["payload"]

    @pytest.mark.asyncio
    async def test_returns_mock_status_correctly(self) -> None:
        emb = FakeEmbedder(ready=True, mock=True, path="/fake/no-bge")
        ws = FakeWebSocket()
        sc = FakeServiceContextWithEmbedder(embedder=emb)
        await p4_ipc.handle(ws, "s1", "embedder_status", {}, sc)
        m = ws.sent[0]
        assert m["payload"]["is_ready"] is True
        assert m["payload"]["is_mock"] is True

    @pytest.mark.asyncio
    async def test_graceful_when_embedder_absent(self) -> None:
        ws = FakeWebSocket()
        sc = FakeServiceContext()  # 没注册 embedder
        await p4_ipc.handle(ws, "s1", "embedder_status", {}, sc)
        m = ws.sent[0]
        assert m["type"] == "embedder_status_response"
        assert m["payload"]["is_ready"] is False
        assert m["payload"]["reason"] == "embedder_not_registered"

    @pytest.mark.asyncio
    async def test_handles_embedder_method_raise(self) -> None:
        emb = FakeEmbedder(raise_on_call=RuntimeError("CUDA OOM"))
        ws = FakeWebSocket()
        sc = FakeServiceContextWithEmbedder(embedder=emb)
        await p4_ipc.handle(ws, "s1", "embedder_status", {}, sc)
        m = ws.sent[0]
        # 必须不抛、必须返回 reason
        assert m["type"] == "embedder_status_response"
        assert m["payload"]["is_ready"] is False
        assert "embedder_error" in m["payload"]["reason"]


# ---------------------------------------------------------------------------
# Membership guard
# ---------------------------------------------------------------------------
def test_message_type_membership() -> None:
    """main.py dispatches via P4_IPC_MESSAGE_TYPES — keep this contract stable."""
    assert "skills_list" in p4_ipc.P4_IPC_MESSAGE_TYPES
    assert "decisions_list" in p4_ipc.P4_IPC_MESSAGE_TYPES
    assert "memory_search" in p4_ipc.P4_IPC_MESSAGE_TYPES
    assert "memory_l1_list" in p4_ipc.P4_IPC_MESSAGE_TYPES
    assert "memory_l1_delete" in p4_ipc.P4_IPC_MESSAGE_TYPES
    assert "embedder_status" in p4_ipc.P4_IPC_MESSAGE_TYPES
    assert len(p4_ipc.P4_IPC_MESSAGE_TYPES) == 6
