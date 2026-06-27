# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-CC-2 — plan mode 物理只读 (execute 层拦截) TDD tests.

验证 registry.execute_tool 在 set_plan_read_only(sid, True) 期间：
  ① 写/执行类 permission_category 工具被 deny，handler **不执行**；
     覆盖 write_file / edit_file / run_shell + 产物工具 (ppt/excel/doc) +
     desktop_write + skill_install。
  ② 只读工具 (read_file / glob / grep / network) 照常放行。
  ③ 非只读模式（默认）全放行 = 字节级 BC。
  ④ 退出只读后写工具恢复可执行（幂等解禁）。

注：不接 PermissionGate —— 只读拦截发生在 gate 之前，且测试用 handler
计数器证明 deny 路径下 handler 从未被调用（物理只读，非流程提示）。
"""
from __future__ import annotations

from typing import Any

import pytest

from deskpet.tools.registry import (
    ToolRegistry,
    _WRITE_PERMISSION_CATEGORIES,
)


# ---------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------


def _make_reg_with_counter() -> tuple[ToolRegistry, dict[str, int]]:
    """注册一组覆盖各 permission_category 的工具，handler 自增计数器。"""
    reg = ToolRegistry()
    calls: dict[str, int] = {}

    def _make_handler(name: str):
        def _h(args: dict[str, Any], tid: str) -> str:
            calls[name] = calls.get(name, 0) + 1
            return '{"ok":true}'

        return _h

    # (tool_name, permission_category) — 既含 os 写类，也含产物写工具，
    # 以及只读类，以证明按 category（非工具名硬编码）判定。
    specs = [
        # ---- 写/执行类（应被拦） ----
        ("write_file", "write_file"),
        ("edit_file", "write_file"),
        ("run_shell", "shell"),
        ("ppt_create", "write_file"),      # 产物写工具
        ("excel_write", "write_file"),     # 产物写工具
        ("doc_write", "write_file"),       # 产物写工具
        ("desktop_create_file", "desktop_write"),
        ("skill_install_tool", "skill_install"),
        # ---- 只读/无副作用类（应放行） ----
        ("read_file", "read_file"),
        ("glob", "read_file"),
        ("grep", "read_file"),
        ("web_search", "network"),
    ]
    for tname, cat in specs:
        reg.register(
            tname,
            "test",
            {"name": tname, "description": tname, "parameters": {}},
            _make_handler(tname),
            permission_category=cat,
        )
    return reg, calls


WRITE_TOOLS = [
    "write_file",
    "edit_file",
    "run_shell",
    "ppt_create",
    "excel_write",
    "doc_write",
    "desktop_create_file",
    "skill_install_tool",
]
READ_TOOLS = ["read_file", "glob", "grep", "web_search"]


# ---------------------------------------------------------------------
# constant sanity
# ---------------------------------------------------------------------


def test_write_categories_constant_covers_all_write_classes() -> None:
    """写类集合必须含 4 个写/执行 category，且不含只读类。"""
    assert _WRITE_PERMISSION_CATEGORIES == frozenset(
        {"write_file", "desktop_write", "shell", "skill_install"}
    )
    for ro in ("read_file", "read_file_sensitive", "network", "mcp_call"):
        assert ro not in _WRITE_PERMISSION_CATEGORIES


# ---------------------------------------------------------------------
# ① plan 只读 → 写类工具 deny + handler 不执行
# ---------------------------------------------------------------------


@pytest.mark.parametrize("tool", WRITE_TOOLS)
@pytest.mark.asyncio
async def test_plan_read_only_denies_write_tools(tool: str) -> None:
    reg, calls = _make_reg_with_counter()
    reg.set_plan_read_only("s1", True)

    out = await reg.execute_tool(tool, {"path": "x", "content": "y"}, "s1")

    assert out["ok"] is False, f"{tool} should be denied"
    assert "规划期只读" in out["error"]
    assert calls.get(tool, 0) == 0, f"{tool} handler must NOT run (物理只读)"


# ---------------------------------------------------------------------
# ② plan 只读 → 只读工具放行
# ---------------------------------------------------------------------


@pytest.mark.parametrize("tool", READ_TOOLS)
@pytest.mark.asyncio
async def test_plan_read_only_allows_read_tools(tool: str) -> None:
    reg, calls = _make_reg_with_counter()
    reg.set_plan_read_only("s1", True)

    out = await reg.execute_tool(tool, {}, "s1")

    assert out["ok"] is True, f"{tool} should be allowed"
    assert calls.get(tool, 0) == 1, f"{tool} handler must run"


# ---------------------------------------------------------------------
# ③ 非只读模式（默认）→ 写工具全放行 (BC)
# ---------------------------------------------------------------------


@pytest.mark.parametrize("tool", WRITE_TOOLS)
@pytest.mark.asyncio
async def test_no_plan_read_only_allows_everything(tool: str) -> None:
    reg, calls = _make_reg_with_counter()
    # 从不调 set_plan_read_only → session 不在只读集 → 字节级 BC

    out = await reg.execute_tool(tool, {"path": "x", "content": "y"}, "s1")

    assert out["ok"] is True, f"{tool} should run when not in plan read-only"
    assert calls.get(tool, 0) == 1


# ---------------------------------------------------------------------
# ④ 只读仅限被置位的 session；退出后恢复
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_read_only_is_per_session() -> None:
    reg, calls = _make_reg_with_counter()
    reg.set_plan_read_only("s1", True)

    # s1 被拦
    out1 = await reg.execute_tool("write_file", {"path": "x", "content": "y"}, "s1")
    assert out1["ok"] is False
    # s2 未置位 → 放行
    out2 = await reg.execute_tool("write_file", {"path": "x", "content": "y"}, "s2")
    assert out2["ok"] is True
    assert calls.get("write_file", 0) == 1


@pytest.mark.asyncio
async def test_plan_read_only_unlocks_after_exit() -> None:
    reg, calls = _make_reg_with_counter()
    reg.set_plan_read_only("s1", True)

    denied = await reg.execute_tool("write_file", {"path": "x", "content": "y"}, "s1")
    assert denied["ok"] is False
    assert calls.get("write_file", 0) == 0

    # 用户点[执行] → 解禁（幂等：再 False 一次无害）
    reg.set_plan_read_only("s1", False)
    reg.set_plan_read_only("s1", False)
    assert reg.is_plan_read_only("s1") is False

    allowed = await reg.execute_tool("write_file", {"path": "x", "content": "y"}, "s1")
    assert allowed["ok"] is True
    assert calls.get("write_file", 0) == 1


def test_is_plan_read_only_reflects_state() -> None:
    reg, _ = _make_reg_with_counter()
    assert reg.is_plan_read_only("s1") is False
    reg.set_plan_read_only("s1", True)
    assert reg.is_plan_read_only("s1") is True
    reg.set_plan_read_only("s1", False)
    assert reg.is_plan_read_only("s1") is False
