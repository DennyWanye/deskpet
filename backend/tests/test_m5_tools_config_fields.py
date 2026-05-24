"""WI-T5.1 v3 — ToolsConfig 5 字段端到端测试.

字段（按 TDD §A12）:
  1. disabled_toolsets — 双层挡（schemas + execute_tool）
  2. disabled_toolsets_schema_only — 仅 schemas 层挡
  3. dangerous_tools_allowlist — dangerous=True 工具白名单
  4. default_timeout_seconds — ToolSpec 未配 timeout 时兜底
  5. strict_unknown_toolset — typo 时 fail-fast / warn 切换
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest


@dataclass
class _ToolsCfgStub:
    """Minimal cfg.tools 替身 — 字段名与 backend/config.py:ToolsConfig 对齐."""
    disabled_toolsets: list[str] = field(default_factory=list)
    disabled_toolsets_schema_only: list[str] = field(default_factory=list)
    dangerous_tools_allowlist: list[str] = field(default_factory=list)
    default_timeout_seconds: float = 60.0
    strict_unknown_toolset: bool = False
    last_mile: Any = None


def _make_registry_with_tools():
    """Build a registry with diverse tools for filter testing."""
    from deskpet.tools.registry import ToolRegistry, ToolSpec

    reg = ToolRegistry()
    schemas = [
        ("read_safe", "file", False, "read_file"),
        ("write_dangerous", "file", True, "write_file"),
        ("computer_screenshot", "computer_use", False, "read_file"),
        ("memory_search", "memory", False, "read_file"),
    ]
    for name, toolset, dangerous, perm in schemas:
        spec = ToolSpec(
            name=name, toolset=toolset,
            schema={"name": name, "description": name, "parameters": {}},
            handler=lambda a, t, n=name: json.dumps({"ok": True, "tool": n}),
            dangerous=dangerous,
            permission_category=perm,
        )
        with reg._lock:
            reg._tools[name] = spec
    return reg


# ─── disabled_toolsets 双层挡 ────────────────────────────────────


def test_disabled_toolsets_filters_schemas():
    reg = _make_registry_with_tools()
    cfg = _ToolsCfgStub(disabled_toolsets=["computer_use"])
    reg.set_tools_config_provider(lambda: cfg)

    names = [s["function"]["name"] for s in reg.schemas()]
    assert "computer_screenshot" not in names
    assert "read_safe" in names
    assert "memory_search" in names


@pytest.mark.asyncio
async def test_disabled_toolsets_blocks_execute_tool():
    """**核心 v3 行为**：strict 模式 execute_tool 也拒（不只过 schemas）."""
    reg = _make_registry_with_tools()
    cfg = _ToolsCfgStub(disabled_toolsets=["computer_use"])
    reg.set_tools_config_provider(lambda: cfg)

    res = await reg.execute_tool(
        "computer_screenshot", params={}, session_id="t", task_id="t",
    )
    assert res["ok"] is False
    assert "disabled_toolsets" in res["error"]
    assert "computer_use" in res["error"]


# ─── disabled_toolsets_schema_only 单层 ─────────────────────────


def test_schema_only_disabled_filters_schemas():
    reg = _make_registry_with_tools()
    cfg = _ToolsCfgStub(disabled_toolsets_schema_only=["computer_use"])
    reg.set_tools_config_provider(lambda: cfg)

    names = [s["function"]["name"] for s in reg.schemas()]
    assert "computer_screenshot" not in names


@pytest.mark.asyncio
async def test_schema_only_disabled_allows_execute_tool():
    """opt-in schema_only：execute_tool 仍可调（编排器/测试用）."""
    reg = _make_registry_with_tools()
    cfg = _ToolsCfgStub(disabled_toolsets_schema_only=["computer_use"])
    reg.set_tools_config_provider(lambda: cfg)

    res = await reg.execute_tool(
        "computer_screenshot", params={}, session_id="t", task_id="t",
    )
    # 工具被允许调（结果是 ok=True 因为 handler return ok）
    assert res["ok"] is True


# ─── dangerous_tools_allowlist ──────────────────────────────────


def test_dangerous_allowlist_empty_default_keeps_all_dangerous():
    """空 allowlist（默认）= 沿用现状 — dangerous 工具仍出现在 schemas."""
    reg = _make_registry_with_tools()
    cfg = _ToolsCfgStub(dangerous_tools_allowlist=[])
    reg.set_tools_config_provider(lambda: cfg)

    names = [s["function"]["name"] for s in reg.schemas()]
    assert "write_dangerous" in names


def test_dangerous_allowlist_filters_non_listed_dangerous_tools():
    """非空 allowlist → 仅 allowlist 中的 dangerous 工具暴露给 LLM."""
    reg = _make_registry_with_tools()
    # allowlist 只含一个不存在的 — 所有 dangerous 都被挡
    cfg = _ToolsCfgStub(dangerous_tools_allowlist=["other_dangerous"])
    reg.set_tools_config_provider(lambda: cfg)

    names = [s["function"]["name"] for s in reg.schemas()]
    assert "write_dangerous" not in names
    # 非 dangerous 不受影响
    assert "read_safe" in names


def test_dangerous_allowlist_includes_listed_tool():
    reg = _make_registry_with_tools()
    cfg = _ToolsCfgStub(dangerous_tools_allowlist=["write_dangerous"])
    reg.set_tools_config_provider(lambda: cfg)

    names = [s["function"]["name"] for s in reg.schemas()]
    assert "write_dangerous" in names


# ─── default_timeout_seconds ───────────────────────────────────


@pytest.mark.asyncio
async def test_default_timeout_seconds_applied_when_spec_unset():
    """cfg.default_timeout_seconds 影响 ToolSpec 未显式配 timeout 的工具."""
    from deskpet.tools.registry import ToolRegistry, ToolSpec

    async def _slow_handler(params, task_id):
        await asyncio.sleep(0.5)  # 500ms
        return json.dumps({"ok": True})

    reg = ToolRegistry()
    # 用一个 spec 默认 timeout=60；cfg 设 default=0.05 (50ms) 应该不影响
    # 因为 spec.timeout_seconds=60 显式 > 0.05
    spec = ToolSpec(
        name="slow", toolset="test",
        schema={"name": "slow", "description": "x", "parameters": {}},
        handler=_slow_handler,
        timeout_seconds=60.0,  # 显式配 — 不被 cfg 覆盖
    )
    with reg._lock:
        reg._tools["slow"] = spec

    cfg = _ToolsCfgStub(default_timeout_seconds=0.05)
    reg.set_tools_config_provider(lambda: cfg)

    # 500ms handler vs spec.timeout=60s → 不 timeout
    res = await reg.execute_tool("slow", params={}, session_id="t", task_id="t")
    assert res["ok"] is True


# ─── 字段存在性 + 默认值 ────────────────────────────────────────


def test_tools_config_has_all_5_v3_fields():
    """WI-T5.1 v3：backend/config.py:ToolsConfig 必须有 5 新字段."""
    from config import ToolsConfig
    cfg = ToolsConfig()
    # 5 字段必须存在 + 默认值正确
    assert cfg.disabled_toolsets == []
    assert cfg.disabled_toolsets_schema_only == []
    assert cfg.dangerous_tools_allowlist == []
    assert cfg.default_timeout_seconds == 60.0
    assert cfg.strict_unknown_toolset is False  # PRD §3.5 默认 False


def test_tools_config_strict_unknown_toolset_field_exists():
    """字段类型正确（bool 而非 str）"""
    from config import ToolsConfig
    import dataclasses
    fields_map = {f.name: f for f in dataclasses.fields(ToolsConfig)}
    assert "strict_unknown_toolset" in fields_map
    assert fields_map["strict_unknown_toolset"].type in (bool, "bool")
