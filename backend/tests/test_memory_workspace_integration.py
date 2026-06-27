# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""TG-6 — 工作记忆接入集成测试（WI-M1.6）。

验证 file_read/file_write（已改 async）成功后经 record_action 落
workspace_state；flag 关时零记录；workspace_recall 工具查回；
WorkspaceMemoryComponent 进 code policy。
"""
from __future__ import annotations

import json

import aiosqlite
import pytest

from deskpet.tools.registry import registry
from deskpet.tools import file_tools
from deskpet.memory.workspace import WorkspaceMemoryStore
from deskpet.memory.memory_v2_schema import _reset_cache_for_tests


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    _reset_cache_for_tests()
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("DESKPET_WORKSPACE_DIR", str(ws))
    db = tmp_path / "state.db"
    yield {"ws": ws, "db": db}
    # 每个用例后摘掉 store，避免污染其它测试。
    file_tools.set_workspace_store(None)


async def _ws_rows(db) -> list[dict]:
    async with aiosqlite.connect(db) as conn:
        conn.row_factory = aiosqlite.Row
        try:
            cur = await conn.execute("SELECT * FROM workspace_state")
        except aiosqlite.OperationalError:
            return []
        rows = await cur.fetchall()
        await cur.close()
    return [dict(r) for r in rows]


async def _table_exists(db, name) -> bool:
    async with aiosqlite.connect(db) as conn:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        )
        row = await cur.fetchone()
        await cur.close()
    return row is not None


# --- T6-1：file_write 成功 → workspace_state action=write ---------------
@pytest.mark.asyncio
async def test_t6_1_file_write_records_workspace_action(sandbox):
    store = WorkspaceMemoryStore(sandbox["db"])
    file_tools.set_workspace_store(store)
    res = json.loads(registry.dispatch(
        "file_write", {"path": "note.md", "content": "hello"}, "sess1",
    ))
    assert res["bytes_written"] == 5
    rows = await _ws_rows(sandbox["db"])
    assert len(rows) == 1
    assert rows[0]["path"] == "note.md"
    assert rows[0]["last_action"] == "write"
    assert rows[0]["session_id"] == "sess1"


# --- T6-2：file_read 同 path → last_action 更新为 read ------------------
@pytest.mark.asyncio
async def test_t6_2_file_read_updates_last_action(sandbox):
    store = WorkspaceMemoryStore(sandbox["db"])
    file_tools.set_workspace_store(store)
    registry.dispatch(
        "file_write", {"path": "a.txt", "content": "x"}, "sess1",
    )
    registry.dispatch("file_read", {"path": "a.txt"}, "sess1")
    rows = await _ws_rows(sandbox["db"])
    assert len(rows) == 1  # 同 (session,path) → upsert，不新增行
    assert rows[0]["last_action"] == "read"


# --- T6-3：flag off（store=None）→ 零记录、无 v2 表 --------------------
@pytest.mark.asyncio
async def test_t6_3_flag_off_zero_record(sandbox):
    file_tools.set_workspace_store(None)
    registry.dispatch(
        "file_write", {"path": "b.txt", "content": "y"}, "sess1",
    )
    assert not await _table_exists(sandbox["db"], "workspace_state")


# --- T6-5：code policy prefer 含 workspace_memory；非 code 不含 --------
def test_t6_5_workspace_memory_in_code_policy():
    from deskpet.agent.assembler.policy import load_policies
    policies = load_policies()
    code = policies["code"]
    assert "workspace_memory" in code.prefer
    # chat policy 不含 workspace_memory。
    assert "workspace_memory" not in policies["chat"].prefer


def test_t6_5b_component_registered_in_default_assembler():
    from deskpet.agent.assembler import build_default_assembler
    asm = build_default_assembler()
    names = asm._registry.names()
    assert "workspace_memory" in names


# --- T6-6：workspace_recall 工具查回本 session 改过的文件 --------------
@pytest.mark.asyncio
async def test_t6_6_workspace_recall_tool(sandbox):
    store = WorkspaceMemoryStore(sandbox["db"])
    file_tools.set_workspace_store(store)
    registry.dispatch(
        "file_write",
        {"path": "report.md", "content": "季度汇报内容"},
        "sess1",
    )
    out = json.loads(registry.dispatch(
        "workspace_recall", {"query": "report"}, "sess1",
    ))
    assert out["count"] == 1
    assert out["matches"][0]["path"] == "report.md"
    assert out["matches"][0]["last_action"] == "write"


# --- T6-6b：flag off 时 workspace_recall 报 disabled --------------------
@pytest.mark.asyncio
async def test_t6_6b_workspace_recall_disabled_when_off(sandbox):
    file_tools.set_workspace_store(None)
    out = json.loads(registry.dispatch(
        "workspace_recall", {"query": "anything"}, "sess1",
    ))
    assert out["reason"] == "workspace_memory_disabled"
