# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-G1 — Teammate tools tests (Companion+Code v2 Multi-Agent Team).

Coverage:
* All 5 tools happy-path JSON envelope
* missing / wrong-type args → ok=False envelope (not raise)
* update_task: status validation enforced at tool level
* send_message: empty content rejected
* store-level exception → safe-fail (handler returns ok=False, no raise)
* schemas are properly shaped (name + parameters)
* FORBIDDEN_TEAMMATE_TOOLS contains the recursion-guard set
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from deskpet.agent.team.team_store import TeamStore
from deskpet.agent.team.teammate_tools import (
    FORBIDDEN_TEAMMATE_TOOLS,
    build_teammate_tools,
)


@pytest.fixture
def store(tmp_path: Path) -> TeamStore:
    return TeamStore(tmp_path / "teams")


def _by_name(tools, name):
    """Convenience: lookup (schema, handler) by tool name."""
    for n, schema, handler in tools:
        if n == name:
            return schema, handler
    raise KeyError(name)


def test_forbidden_set_blocks_recursion() -> None:
    assert "agent" in FORBIDDEN_TEAMMATE_TOOLS
    assert "agent_parallel" in FORBIDDEN_TEAMMATE_TOOLS
    assert "spawn_team" in FORBIDDEN_TEAMMATE_TOOLS


@pytest.mark.asyncio
async def test_build_returns_five_tools(store: TeamStore) -> None:
    tools = build_teammate_tools(store=store, team_id="t1", teammate_id="tm1")
    names = {n for n, _, _ in tools}
    assert names == {
        "team_task_create",
        "team_task_claim",
        "team_task_update",
        "team_task_list",
        "team_send_message",
    }


@pytest.mark.asyncio
async def test_schemas_have_required_fields(store: TeamStore) -> None:
    tools = build_teammate_tools(store=store, team_id="t1", teammate_id="tm1")
    for name, schema, _ in tools:
        assert schema["name"] == name
        assert "description" in schema
        assert "parameters" in schema
        assert schema["parameters"].get("type") == "object"


@pytest.mark.asyncio
async def test_create_happy_path(store: TeamStore) -> None:
    tools = build_teammate_tools(store=store, team_id="t1", teammate_id="tm1")
    _schema, h = _by_name(tools, "team_task_create")
    raw = await h({"description": "do A"}, "")
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert isinstance(payload["task_id"], str) and len(payload["task_id"]) == 32
    # Round-trip via store
    tasks = await store.list_tasks("t1")
    assert len(tasks) == 1 and tasks[0].description == "do A"


@pytest.mark.asyncio
async def test_create_missing_description_envelope(store: TeamStore) -> None:
    tools = build_teammate_tools(store=store, team_id="t1", teammate_id="tm1")
    _schema, h = _by_name(tools, "team_task_create")
    raw = await h({}, "")
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "description" in payload["error"]


@pytest.mark.asyncio
async def test_create_empty_string_rejected(store: TeamStore) -> None:
    tools = build_teammate_tools(store=store, team_id="t1", teammate_id="tm1")
    _schema, h = _by_name(tools, "team_task_create")
    raw = await h({"description": "   "}, "")
    payload = json.loads(raw)
    assert payload["ok"] is False


@pytest.mark.asyncio
async def test_claim_empty_pool_returns_null(store: TeamStore) -> None:
    tools = build_teammate_tools(store=store, team_id="t1", teammate_id="tm1")
    _schema, h = _by_name(tools, "team_task_claim")
    raw = await h({}, "")
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["task"] is None


@pytest.mark.asyncio
async def test_claim_returns_task_dict(store: TeamStore) -> None:
    await store.create_task("t1", "work")
    tools = build_teammate_tools(store=store, team_id="t1", teammate_id="tm-alice")
    _schema, h = _by_name(tools, "team_task_claim")
    raw = await h({}, "")
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["task"]["description"] == "work"
    assert payload["task"]["claimed_by"] == "tm-alice"


@pytest.mark.asyncio
async def test_update_happy_path(store: TeamStore) -> None:
    tid = await store.create_task("t1", "x")
    await store.claim_task("t1", "tm1")
    tools = build_teammate_tools(store=store, team_id="t1", teammate_id="tm1")
    _schema, h = _by_name(tools, "team_task_update")
    raw = await h(
        {"task_id": tid, "status": "done", "result": "all done"},
        "",
    )
    payload = json.loads(raw)
    assert payload == {"ok": True, "updated": True}


@pytest.mark.asyncio
async def test_update_missing_task_id(store: TeamStore) -> None:
    tools = build_teammate_tools(store=store, team_id="t1", teammate_id="tm1")
    _schema, h = _by_name(tools, "team_task_update")
    raw = await h({"status": "done"}, "")
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "task_id" in payload["error"]


@pytest.mark.asyncio
async def test_update_invalid_status_envelope(store: TeamStore) -> None:
    tid = await store.create_task("t1", "x")
    tools = build_teammate_tools(store=store, team_id="t1", teammate_id="tm1")
    _schema, h = _by_name(tools, "team_task_update")
    raw = await h({"task_id": tid, "status": "completed"}, "")
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "in_progress" in payload["error"]


@pytest.mark.asyncio
async def test_update_unknown_task_id(store: TeamStore) -> None:
    tools = build_teammate_tools(store=store, team_id="t1", teammate_id="tm1")
    _schema, h = _by_name(tools, "team_task_update")
    raw = await h(
        {"task_id": "deadbeef", "status": "done", "result": "x"}, ""
    )
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "not found" in payload["error"]


@pytest.mark.asyncio
async def test_list_returns_tasks(store: TeamStore) -> None:
    await store.create_task("t1", "A")
    await store.create_task("t1", "B")
    tools = build_teammate_tools(store=store, team_id="t1", teammate_id="tm1")
    _schema, h = _by_name(tools, "team_task_list")
    raw = await h({}, "")
    payload = json.loads(raw)
    assert payload["ok"] is True
    descs = [t["description"] for t in payload["tasks"]]
    assert descs == ["A", "B"]


@pytest.mark.asyncio
async def test_list_filter_pending(store: TeamStore) -> None:
    await store.create_task("t1", "A")
    await store.create_task("t1", "B")
    await store.claim_task("t1", "tm1")
    tools = build_teammate_tools(store=store, team_id="t1", teammate_id="tm1")
    _schema, h = _by_name(tools, "team_task_list")
    raw = await h({"status": "pending"}, "")
    payload = json.loads(raw)
    descs = [t["description"] for t in payload["tasks"]]
    assert descs == ["B"]


@pytest.mark.asyncio
async def test_list_invalid_status_type(store: TeamStore) -> None:
    tools = build_teammate_tools(store=store, team_id="t1", teammate_id="tm1")
    _schema, h = _by_name(tools, "team_task_list")
    raw = await h({"status": 123}, "")
    payload = json.loads(raw)
    assert payload["ok"] is False


@pytest.mark.asyncio
async def test_send_message_happy_path(store: TeamStore) -> None:
    tools = build_teammate_tools(store=store, team_id="t1", teammate_id="tm1")
    _schema, h = _by_name(tools, "team_send_message")
    raw = await h({"to_id": "tm2", "content": "hi there"}, "")
    payload = json.loads(raw)
    assert payload == {"ok": True, "sent": True}
    msgs = await store.get_messages("t1", "tm2")
    assert len(msgs) == 1
    assert msgs[0].from_id == "tm1"
    assert msgs[0].content == "hi there"


@pytest.mark.asyncio
async def test_send_message_missing_recipient(store: TeamStore) -> None:
    tools = build_teammate_tools(store=store, team_id="t1", teammate_id="tm1")
    _schema, h = _by_name(tools, "team_send_message")
    raw = await h({"content": "x"}, "")
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "to_id" in payload["error"]


@pytest.mark.asyncio
async def test_send_message_empty_content(store: TeamStore) -> None:
    tools = build_teammate_tools(store=store, team_id="t1", teammate_id="tm1")
    _schema, h = _by_name(tools, "team_send_message")
    raw = await h({"to_id": "tm2", "content": ""}, "")
    payload = json.loads(raw)
    assert payload["ok"] is False


@pytest.mark.asyncio
async def test_store_exception_is_safefail() -> None:
    """If the underlying store raises a non-aiosqlite exception, the
    handler must still return an ok=False envelope (not raise)."""

    class _BrokenStore:
        async def create_task(self, *_a, **_k):
            raise RuntimeError("boom")

    tools = build_teammate_tools(
        store=_BrokenStore(),  # type: ignore[arg-type]
        team_id="t1",
        teammate_id="tm1",
    )
    _schema, h = _by_name(tools, "team_task_create")
    raw = await h({"description": "x"}, "")
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "RuntimeError" in payload["error"]
