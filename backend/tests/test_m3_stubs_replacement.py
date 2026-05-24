"""WI-T3.1/T3.2/T3.3 v3 — stubs.py 替换真实现验证.

T3.1: memory_write / memory_read / memory_search 真接 facts.py
T3.2: skill_invoke 真接 SkillLoader.invoke_script
T3.3: mcp_call / delegate 直接 unregister（stubs.py 不再注册）
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest


# ─── T3.1 memory_* 真实现 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_t3_1_memory_write_translates_tier_to_category(tmp_path):
    """memory_write tier 翻译表生效（PRD v3 D17）."""
    from deskpet.memory.facts import FactsStore
    from deskpet.tools import memory_tools

    db_path = tmp_path / "facts.db"
    store = FactsStore(db_path=str(db_path), embedder=None)

    # 注入真 facts_store（绕过 main.py bind）
    memory_tools.bind(facts_store=store, embedder=None, llm_call=None)

    # 调真 handler
    handler = memory_tools._memory_write_handle
    res = json.loads(await handler(
        {"text": "用户喜欢深色主题", "tier": "l3", "salience": 0.9},
        task_id="t31",
    ))
    assert res["ok"] is True
    assert res["category"] == "preference"  # l3 → preference
    assert res["tier"] == "l3"
    assert isinstance(res["memory_id"], int)


@pytest.mark.asyncio
async def test_t3_1_memory_read_by_id_round_trip(tmp_path):
    """memory_write → memory_read by id → 读回原内容."""
    from deskpet.memory.facts import FactsStore
    from deskpet.tools import memory_tools

    store = FactsStore(db_path=str(tmp_path / "f.db"), embedder=None)
    memory_tools.bind(facts_store=store, embedder=None, llm_call=None)

    write_res = json.loads(await memory_tools._memory_write_handle(
        {"text": "项目代号 Poseidon", "tier": "l2"}, task_id="t",
    ))
    assert write_res["ok"]
    mid = write_res["memory_id"]

    read_res = json.loads(await memory_tools._memory_read_handle(
        {"memory_id": mid}, task_id="t",
    ))
    assert read_res["ok"] is True
    assert read_res["fact"]["value"] == "项目代号 Poseidon"
    assert read_res["fact"]["category"] == "project"  # l2 → project


@pytest.mark.asyncio
async def test_t3_1_memory_read_not_found(tmp_path):
    from deskpet.memory.facts import FactsStore
    from deskpet.tools import memory_tools

    store = FactsStore(db_path=str(tmp_path / "f.db"), embedder=None)
    memory_tools.bind(facts_store=store, embedder=None, llm_call=None)

    res = json.loads(await memory_tools._memory_read_handle(
        {"memory_id": 999999}, task_id="t",
    ))
    assert res["ok"] is False
    assert "not found" in res["error"]


@pytest.mark.asyncio
async def test_t3_1_memory_search_real_returns_results(tmp_path):
    """memory_write 多条 → memory_search query → 真返结果."""
    from deskpet.memory.facts import FactsStore
    from deskpet.tools import memory_tools

    store = FactsStore(db_path=str(tmp_path / "f.db"), embedder=None)
    memory_tools.bind(facts_store=store, embedder=None, llm_call=None)

    await memory_tools._memory_write_handle({"text": "苹果是水果"}, task_id="t")
    await memory_tools._memory_write_handle({"text": "西瓜是水果"}, task_id="t")
    await memory_tools._memory_write_handle({"text": "猫是动物"}, task_id="t")

    res = json.loads(await memory_tools._memory_search_handle(
        {"query": "水果", "top_k": 5}, task_id="t",
    ))
    assert res["ok"] is True
    assert res["count"] >= 2
    values = [r["value"] for r in res["results"]]
    assert any("苹果" in v for v in values)
    assert any("西瓜" in v for v in values)


def test_t3_1_facts_store_has_get_by_id():
    """R-MISS-9：facts.py 必须提供 get_by_id 方法."""
    from deskpet.memory.facts import FactsStore
    assert hasattr(FactsStore, "get_by_id")
    import inspect
    assert inspect.iscoroutinefunction(FactsStore.get_by_id)


def test_t3_1_tier_to_category_table_correct():
    """PRD v3 D17：翻译表语义正确（l1=event,l2=project,l3=preference)."""
    from deskpet.tools.memory_tools import _TIER_TO_CATEGORY
    assert _TIER_TO_CATEGORY["l1"] == "event"     # 短期/快衰减
    assert _TIER_TO_CATEGORY["l2"] == "project"   # 中期
    assert _TIER_TO_CATEGORY["l3"] == "preference"  # 长期/慢衰减
    assert _TIER_TO_CATEGORY["auto"] == "preference"  # 保守默认


# ─── T3.2 skill_invoke 真实现 ────────────────────────────────────


def test_t3_2_skill_invoke_registered_as_real_not_stub():
    """skill_invoke 已真注册（toolset=control，不是 stubs 占位）."""
    from deskpet.tools.registry import registry
    spec = registry._tools.get("skill_invoke")
    assert spec is not None
    assert spec.toolset == "control"
    # source=builtin 真实现；如果是 stub 守卫模式注册则 source 也 builtin，
    # 但 handler module 不同
    assert spec.handler.__module__ == "deskpet.tools.skill_tools", (
        f"skill_invoke handler from {spec.handler.__module__}, "
        f"expected deskpet.tools.skill_tools (T3.2 real impl)"
    )


@pytest.mark.asyncio
async def test_t3_2_skill_invoke_not_bound_returns_error():
    """skill_loader 未注入 → 返 not bound 错（不崩）."""
    from deskpet.tools import skill_tools
    # Reset (test isolation)
    skill_tools._skill_loader = None
    res = json.loads(await skill_tools._handle(
        {"skill_name": "anything"}, task_id="t",
    ))
    assert res["ok"] is False
    assert "not bound" in res["error"]


@pytest.mark.asyncio
async def test_t3_2_skill_invoke_missing_name_validation():
    from deskpet.tools import skill_tools

    # mock skill_loader 让 not bound 不阻路径
    class _Fake:
        async def invoke_script(self, name, args=None):
            return "{}"

    skill_tools.bind(skill_loader=_Fake())
    res = json.loads(await skill_tools._handle({}, task_id="t"))
    assert res["ok"] is False
    assert "skill_name" in res["error"]


# ─── T3.3 mcp_call / delegate unregister ─────────────────────────


def test_t3_3_mcp_call_not_registered():
    """T3.3 v3 D10：mcp_call 直接删（无真 caller，0-release）."""
    from deskpet.tools.registry import registry
    assert "mcp_call" not in registry._tools, (
        "WI-T3.3: mcp_call still registered as stub. stubs.py should NOT register it."
    )


def test_t3_3_delegate_not_registered():
    from deskpet.tools.registry import registry
    assert "delegate" not in registry._tools, (
        "WI-T3.3: delegate still registered as stub."
    )


def test_t3_3_mcp_namespace_qualified_names_still_supported():
    """删 mcp_call 不影响 MCP 真工具 (`mcp_<server>_<tool>`) 注册."""
    from deskpet.tools.registry import ToolRegistry, ToolNameConflictError

    reg = ToolRegistry()
    # 模拟 MCP 注册 qualified 名
    reg.register(
        "mcp_localfs_read_file", "mcp",
        {"name": "mcp_localfs_read_file", "description": "x", "parameters": {}},
        lambda a, t: "{}",
        source="mcp:localfs",
        replace_allowed=True,
    )
    assert "mcp_localfs_read_file" in reg._tools
