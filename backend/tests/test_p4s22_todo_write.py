# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S22 — todo_write tool + SessionDB v11 schema.

Two interlocking layers:
  * SessionDB.replace_code_todos / get_code_todos round-trip
  * todo_write tool handler validates + bridges sync → async write
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from deskpet.memory.session_db import SessionDB
from deskpet.tools.code_tools.todo_write_tool import build_todo_write_tool


# ---------------------------------------------------------------------------
# SessionDB v11 — schema migration + replace/get round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v11_table_exists_after_initialize(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    items = await db.get_code_todos("any-session")
    assert items == []


@pytest.mark.asyncio
async def test_replace_code_todos_round_trip(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    await db.replace_code_todos(
        "code-deadbeef",
        [
            {"content": "plan", "activeForm": "planning", "status": "completed"},
            {"content": "build", "activeForm": "building", "status": "in_progress"},
            {"content": "test", "activeForm": "testing", "status": "pending"},
        ],
    )
    items = await db.get_code_todos("code-deadbeef")
    assert len(items) == 3
    assert items[0]["content"] == "plan"
    assert items[1]["status"] == "in_progress"
    assert [it["sort_order"] for it in items] == [0, 1, 2]


@pytest.mark.asyncio
async def test_replace_code_todos_overwrites(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    await db.replace_code_todos(
        "s1",
        [{"content": "a", "activeForm": "a-ing", "status": "pending"}],
    )
    await db.replace_code_todos(
        "s1",
        [
            {"content": "x", "activeForm": "x-ing", "status": "completed"},
            {"content": "y", "activeForm": "y-ing", "status": "pending"},
        ],
    )
    items = await db.get_code_todos("s1")
    assert len(items) == 2
    assert items[0]["content"] == "x"


@pytest.mark.asyncio
async def test_session_isolation(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    await db.replace_code_todos(
        "s1", [{"content": "for s1", "activeForm": "ing", "status": "pending"}]
    )
    await db.replace_code_todos(
        "s2", [{"content": "for s2", "activeForm": "ing", "status": "pending"}]
    )
    s1_items = await db.get_code_todos("s1")
    s2_items = await db.get_code_todos("s2")
    assert s1_items[0]["content"] == "for s1"
    assert s2_items[0]["content"] == "for s2"


@pytest.mark.asyncio
async def test_invalid_status_falls_back_to_pending(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    await db.replace_code_todos(
        "s1",
        [{"content": "x", "activeForm": "x", "status": "invalid"}],
    )
    items = await db.get_code_todos("s1")
    assert items[0]["status"] == "pending"


# ---------------------------------------------------------------------------
# todo_write tool — validation + sync bridge
# ---------------------------------------------------------------------------


def test_todo_write_tool_creates_records(tmp_path):
    """Run the tool from a synchronous context. The handler internally
    bridges to asyncio via asyncio.run since no loop is running here."""
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    asyncio.run(db.initialize())

    handler, schema = build_todo_write_tool(
        session_db=db,
        code_session_id_resolver=lambda: "code-test",
        broadcaster=None,
    )

    out = json.loads(
        handler(
            {
                "items": [
                    {"content": "task1", "activeForm": "doing task1", "status": "in_progress"},
                    {"content": "task2", "activeForm": "doing task2", "status": "pending"},
                ]
            }
        )
    )
    assert out["ok"] is True
    assert out["count"] == 2

    # Verify persistence
    items = asyncio.run(db.get_code_todos("code-test"))
    assert len(items) == 2
    assert items[0]["content"] == "task1"


def test_todo_write_rejects_when_code_mode_off(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    asyncio.run(db.initialize())
    handler, _ = build_todo_write_tool(
        session_db=db,
        code_session_id_resolver=lambda: None,  # code mode off
        broadcaster=None,
    )
    out = json.loads(handler({"items": [{"content": "x", "activeForm": "x", "status": "pending"}]}))
    assert "error" in out
    assert "code mode" in out["error"]


def test_todo_write_rejects_non_list_items(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    asyncio.run(db.initialize())
    handler, _ = build_todo_write_tool(
        session_db=db,
        code_session_id_resolver=lambda: "code-x",
        broadcaster=None,
    )
    out = json.loads(handler({"items": "not a list"}))
    assert "error" in out


def test_todo_write_handles_missing_active_form(tmp_path):
    """activeForm absent → fallback to content (not an error)."""
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    asyncio.run(db.initialize())
    handler, _ = build_todo_write_tool(
        session_db=db,
        code_session_id_resolver=lambda: "code-test",
        broadcaster=None,
    )
    out = json.loads(
        handler({"items": [{"content": "no active", "status": "pending"}]})
    )
    assert out["ok"] is True
    items = asyncio.run(db.get_code_todos("code-test"))
    assert items[0]["activeForm"] == "no active"


@pytest.mark.asyncio
async def test_broadcaster_called_with_todo_update(tmp_path):
    """When a broadcaster is wired, todo_write fires `code_todo_update`."""
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()

    captured: list[dict] = []

    async def _bcast(msg: dict):
        captured.append(msg)

    handler, _ = build_todo_write_tool(
        session_db=db,
        code_session_id_resolver=lambda: "code-broad",
        broadcaster=_bcast,
    )

    # Run handler from inside an async context (the tool has special
    # logic for that case via run_coroutine_threadsafe).
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        handler,
        {"items": [{"content": "a", "activeForm": "a-ing", "status": "pending"}]},
    )
    # Drain any pending tasks
    await asyncio.sleep(0.05)

    assert len(captured) == 1
    assert captured[0]["type"] == "code_todo_update"
    assert captured[0]["payload"]["session_id"] == "code-broad"
    assert len(captured[0]["payload"]["items"]) == 1
