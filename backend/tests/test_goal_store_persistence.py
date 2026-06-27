# SPDX-License-Identifier: BUSL-1.1
"""WI-1.1 / T1 — SessionGoal 扩字段 + 持久化 + 重启恢复。"""
from __future__ import annotations

import pytest

from deskpet.agent.goal_store import SessionGoal, SessionGoalStore
from deskpet.commands import _handle_goal
from deskpet.memory.session_db import SessionDB
from deskpet.memory.memory_v2_schema import _reset_cache_for_tests


@pytest.fixture(autouse=True)
def _reset_schema_cache():
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


def test_new_fields_have_bc_defaults():
    g = SessionGoal(session_id="s1", text="t", set_at=1.0)
    assert g.goal_id == ""
    assert g.status == "active"
    assert g.progress == 0.0
    assert g.criteria is None
    assert g.updated_at == 0.0
    assert g.subgoals == []
    assert g.done is False


def test_set_resets_status_and_done():
    store = SessionGoalStore()
    g = store.set("s1", "目标A")
    assert g.status == "active"
    assert g.done is False


def test_mark_done_sets_both_done_and_status():
    store = SessionGoalStore()
    store.set("s1", "目标A")
    assert store.mark_done("s1") is True
    g = store.get("s1")
    assert g.done is True
    assert g.status == "done"


# ───────────────────── Task 4: 持久化 + 重启恢复 ─────────────────────
@pytest.mark.asyncio
async def test_persist_then_reload_simulates_restart(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    # 进程 1：set + 落库
    store1 = SessionGoalStore()
    store1.bind_persistence(db)
    g = store1.set("s1", "整理三个会议纪要")
    await store1.persist(g)
    # 进程 2：新 store，load_persisted 灌回
    store2 = SessionGoalStore()
    store2.bind_persistence(db)
    await store2.load_persisted()
    restored = store2.get("s1")
    assert restored is not None
    assert restored.text == "整理三个会议纪要"
    assert restored.goal_id == g.goal_id
    assert store2.get_goal_text("s1") == "整理三个会议纪要"


def test_get_goal_text_none_safe():
    store = SessionGoalStore()
    assert store.get_goal_text("nope") is None   # 无目标 → None


@pytest.mark.asyncio
async def test_unbound_store_is_pure_memory(tmp_path):
    store = SessionGoalStore()           # 未 bind_persistence
    g = store.set("s1", "t")
    await store.persist(g)               # 不应抛（safe no-op）
    assert store.get_goal_text("s1") == "t"


@pytest.mark.asyncio
async def test_persist_failure_safe_fail(tmp_path, monkeypatch):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    store = SessionGoalStore()
    store.bind_persistence(db)
    g = store.set("s1", "t")

    async def _boom(**kwargs):
        raise RuntimeError("disk full")
    monkeypatch.setattr(db, "upsert_session_goal", _boom)
    # safe-fail：不抛，内存仍可读
    await store.persist(g)
    assert store.get_goal_text("s1") == "t"


# ───────────────────── I-1: done 终态落库（重启不复活） ─────────────
@pytest.mark.asyncio
async def test_done_goal_does_not_resurrect_after_restart(tmp_path):
    """完成的目标落 done 终态后，重启 load_persisted 不召回（否则只查
    status='active' 会让已完成目标复活成 active）。"""
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    store1 = SessionGoalStore()
    store1.bind_persistence(db)
    g = store1.set("s1", "整理纪要")
    await store1.persist(g)
    store1.mark_done("s1")
    await store1.persist_done("s1")
    store2 = SessionGoalStore()
    store2.bind_persistence(db)
    n = await store2.load_persisted()
    assert store2.get("s1") is None        # 不复活
    assert n == 0


# ───────────────────── Task 5: increment_iteration 落库 ─────────────
@pytest.mark.asyncio
async def test_increment_iteration_persists(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    store1 = SessionGoalStore()
    store1.bind_persistence(db)
    g = store1.set("s1", "t")
    await store1.persist(g)
    # 模拟 2 轮 rebound
    store1.increment_iteration("s1")
    await store1.persist_iteration("s1")
    store1.increment_iteration("s1")
    await store1.persist_iteration("s1")
    # 重启恢复
    store2 = SessionGoalStore()
    store2.bind_persistence(db)
    await store2.load_persisted()
    assert store2.get("s1").iterations_used == 2     # 非归零


# ───────────────────── Task 6: _handle_goal async 落库 ──────────────
@pytest.mark.asyncio
async def test_handle_goal_set_persists(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    store = SessionGoalStore()
    store.bind_persistence(db)
    res = await _handle_goal("整理会议纪要", "s1", store)
    assert res["type"] == "goal_set"
    # 落库验证：新 store 恢复得到
    store2 = SessionGoalStore()
    store2.bind_persistence(db)
    await store2.load_persisted()
    assert store2.get_goal_text("s1") == "整理会议纪要"


@pytest.mark.asyncio
async def test_handle_goal_disabled_when_store_none():
    res = await _handle_goal("x", "s1", None)
    assert res["type"] == "error"


@pytest.mark.asyncio
async def test_handle_goal_clear_persists_abandoned(tmp_path):
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    store = SessionGoalStore()
    store.bind_persistence(db)
    await _handle_goal("目标X", "s1", store)
    # clear → 落 abandoned（不物理删库行）
    res = await _handle_goal("clear", "s1", store)
    assert res["type"] == "goal_cleared"
    # 库里该行不再 active（变 abandoned），新 store load 不到
    store2 = SessionGoalStore()
    store2.bind_persistence(db)
    n = await store2.load_persisted()
    assert n == 0
    assert store2.get_goal_text("s1") is None
