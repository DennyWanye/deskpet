# SPDX-License-Identifier: BUSL-1.1
"""WI-TG-1 — goal_task_create + 主 agent 全局可见性 + 反查解析。

覆盖：
  ① create → list 往返（带 depends_on 的任务建好后 list 能查到）。
  ② 主 agent 可调：goal_mode ON 时 registry 含 goal_task_create。
  ③ goal_mode OFF：registry 无 goal_task_create（字节 BC）。
  ④ 反查解析：create 的 goal_id/session_id 来自活跃目标；无活跃目标
     时返回友好 "请先用 /goal 设定目标" 错误。
"""
from __future__ import annotations

import json

import pytest

from deskpet.agent.goal_store import SessionGoalStore
from deskpet.agent.task_graph import TaskGraphStore
from deskpet.memory.session_db import SessionDB
from deskpet.memory.memory_v2_schema import _reset_cache_for_tests
from deskpet.tools.registry import ToolRegistry
from deskpet.tools.task_graph_tools import build_global_goal_task_tools


@pytest.fixture(autouse=True)
def _reset_schema_cache():
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


async def _call(registry, tool, args, session_id):
    """execute_tool → 解析 handler 返回的 JSON result。"""
    env = await registry.execute_tool(tool, args, session_id=session_id)
    return json.loads(env["result"])


async def _make_store(tmp_path) -> tuple[TaskGraphStore, SessionGoalStore]:
    db = SessionDB(db_path=str(tmp_path / "state.db"))
    await db.initialize()
    tg = TaskGraphStore(db=db)
    gs = SessionGoalStore()
    gs.bind_persistence(db)
    return tg, gs


def _register_global_goal_tasks(
    registry: ToolRegistry,
    tg: TaskGraphStore,
    gs: SessionGoalStore,
) -> None:
    """Mirror main.py 的 goal_mode-ON 注册逻辑（against a fresh registry）。"""
    for name, schema, handler in build_global_goal_task_tools(
        task_graph_store=tg,
        goal_resolver=lambda: gs.get_active_goal_context(),
    ):
        registry.register(
            name=name,
            toolset="goal",
            schema=schema,
            handler=handler,
            concurrency_safe=name in ("goal_task_list", "goal_task_get"),
            replace_allowed=True,
        )


# ───────────────────── ① create → list 往返 ─────────────────────────
@pytest.mark.asyncio
async def test_create_then_list_roundtrip_with_depends_on(tmp_path):
    tg, gs = await _make_store(tmp_path)
    gs.set("s1", "整理三个会议纪要")

    registry = ToolRegistry()
    _register_global_goal_tasks(registry, tg, gs)

    # create base task
    r1 = await _call(registry, "goal_task_create", {"title": "读纪要A"}, "s1")
    assert r1["ok"] is True and r1["created"] is True
    base_id = r1["task_id"]

    # create dependent task with depends_on + note
    r2 = await _call(
        registry,
        "goal_task_create",
        {"title": "汇总", "depends_on": [base_id], "note": "等A读完"},
        "s1",
    )
    assert r2["ok"] is True
    assert r2["depends_on"] == [base_id]
    dep_id = r2["task_id"]

    # list → both visible, dependency + note persisted
    lst = await _call(registry, "goal_task_list", {}, "s1")
    assert lst["ok"] is True
    by_id = {t["task_id"]: t for t in lst["tasks"]}
    assert base_id in by_id and dep_id in by_id
    assert by_id[dep_id]["depends_on"] == [base_id]
    assert by_id[dep_id]["result"] == "等A读完"


# ───────────────────── ② 主 agent 可调（goal_mode ON）─────────────────
@pytest.mark.asyncio
async def test_main_agent_registry_has_goal_task_create_when_on(tmp_path):
    tg, gs = await _make_store(tmp_path)
    registry = ToolRegistry()
    _register_global_goal_tasks(registry, tg, gs)

    names = set(registry.list_tools())
    assert "goal_task_create" in names
    assert "goal_task_list" in names
    assert "goal_task_update" in names
    assert "goal_task_get" in names

    # schema 真出现在主 agent 的 anthropic/openai schema 导出里
    schema_names = {
        s["function"]["name"] for s in registry.to_openai_schema()
    }
    assert "goal_task_create" in schema_names


# ───────────────────── ③ goal_mode OFF → registry 无此工具（BC）──────
def test_registry_has_no_goal_task_create_when_off():
    """goal_mode OFF 时不调用注册逻辑 → registry 不含 goal_task_* （字节 BC）。"""
    registry = ToolRegistry()  # 模拟 flag OFF：从不注册
    names = set(registry.list_tools())
    assert "goal_task_create" not in names
    assert "goal_task_list" not in names
    assert "goal_task_update" not in names
    assert "goal_task_get" not in names
    # dispatch 未知工具 → unknown tool（不是静默成功）
    out = json.loads(registry.dispatch("goal_task_create", {"title": "x"}))
    assert "unknown tool" in out["error"]


# ───────────────────── ④ 反查解析正确 + 无目标友好报错 ───────────────
@pytest.mark.asyncio
async def test_create_resolves_active_goal_id_and_session(tmp_path):
    tg, gs = await _make_store(tmp_path)
    g = gs.set("s7", "写季度报告")  # active goal → goal_id + session_id

    registry = ToolRegistry()
    _register_global_goal_tasks(registry, tg, gs)

    r = await _call(registry, "goal_task_create", {"title": "搭框架"}, "s7")
    assert r["ok"] is True
    # 反查取到的 goal_id 应与活跃目标一致 → 任务落在该 goal 下
    rows = await tg.list(g.goal_id)
    assert len(rows) == 1
    assert rows[0].title == "搭框架"


@pytest.mark.asyncio
async def test_create_friendly_error_when_no_active_goal(tmp_path):
    tg, gs = await _make_store(tmp_path)  # 未 set 任何目标
    registry = ToolRegistry()
    _register_global_goal_tasks(registry, tg, gs)

    r = await _call(registry, "goal_task_create", {"title": "孤儿任务"}, "nope")
    assert r["ok"] is False
    assert "/goal" in r["error"]


@pytest.mark.asyncio
async def test_list_and_update_also_guard_no_active_goal(tmp_path):
    tg, gs = await _make_store(tmp_path)
    registry = ToolRegistry()
    _register_global_goal_tasks(registry, tg, gs)

    for tool, args in (
        ("goal_task_list", {}),
        ("goal_task_update", {"task_id": "x", "status": "done"}),
        ("goal_task_get", {"task_id": "x"}),
    ):
        r = await _call(registry, tool, args, "nope")
        assert r["ok"] is False
        assert "/goal" in r["error"]


# ───────────────────── 边角：create 校验 + cycle 拒绝 ─────────────────
@pytest.mark.asyncio
async def test_create_rejects_empty_title_and_bad_depends(tmp_path):
    tg, gs = await _make_store(tmp_path)
    gs.set("s1", "目标")
    registry = ToolRegistry()
    _register_global_goal_tasks(registry, tg, gs)

    r1 = await _call(registry, "goal_task_create", {"title": "  "}, "s1")
    assert r1["ok"] is False and "title" in r1["error"]

    r2 = await _call(
        registry,
        "goal_task_create",
        {"title": "ok", "depends_on": "not-a-list"},
        "s1",
    )
    assert r2["ok"] is False and "depends_on" in r2["error"]
