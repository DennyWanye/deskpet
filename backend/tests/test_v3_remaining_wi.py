# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-T2.5 / WI-T4.2 / WI-T5.1 子项 测试.

WI-T2.5: vitest CI workflow 存在性 — 文档级（CI 配置文件检查）
WI-T4.2: registry.register 时 source=plugin:xxx 自动加 <plugin>_ 前缀
WI-T5.1 子项: load_config _cached 单例 + mtime 失效
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest


# ─── WI-T2.5: frontend CI workflow 存在 ─────────────────────────


def test_t2_5_frontend_ci_workflow_exists():
    """v3 WI-T2.5：.github/workflows/frontend-tests.yml 存在且含 vitest step."""
    workflow_path = (
        Path(__file__).parent.parent.parent
        / ".github" / "workflows" / "frontend-tests.yml"
    )
    assert workflow_path.exists(), (
        f"WI-T2.5: 期待 frontend CI workflow at {workflow_path}"
    )
    body = workflow_path.read_text(encoding="utf-8")
    assert "vitest" in body.lower(), "workflow 必须含 vitest 步骤"
    # 默认必跑：跳过 # 注释行后检查 run: 命令里不应有 --no-vitest skip
    runtime_lines = [
        ln for ln in body.splitlines()
        if ln.strip().startswith("run:") or (
            ln.strip().startswith("- ") and "npm" in ln
        )
    ]
    runtime_body = "\n".join(runtime_lines)
    assert "--no-vitest" not in runtime_body, (
        "WI-T2.5 v3：vitest CI 默认必跑，run: 步骤里不应有 --no-vitest skip flag"
    )
    assert "npm test" in body or "npm run test" in body, "应有 npm test 调用"


def test_t2_5_last_mile_smoke_default_runs_vitest():
    """last_mile_smoke.py 默认（无 --no-vitest）必跑 check_mr0_vitest."""
    smoke_path = (
        Path(__file__).parent.parent.parent
        / "scripts" / "acceptance" / "last_mile_smoke.py"
    )
    if not smoke_path.exists():
        pytest.skip("last_mile_smoke.py not found")
    body = smoke_path.read_text(encoding="utf-8")
    # 关键守门：if not args.no_vitest: check_mr0_vitest
    assert "if not args.no_vitest" in body
    assert "check_mr0_vitest" in body


# ─── WI-T4.2: plugin 前缀自动加 ──────────────────────────────


def test_t4_2_plugin_source_auto_prefix():
    """source=plugin:notion 时，name=search_pages 自动改成 plugin_notion_search_pages."""
    from deskpet.tools.registry import ToolRegistry, ToolSpec

    reg = ToolRegistry()
    reg.register(
        "search_pages", "control",
        {"name": "search_pages", "description": "x", "parameters": {}},
        lambda a, t: "{}",
        source="plugin:notion",
    )
    # 真注册的 name 已加前缀
    assert "search_pages" not in reg._tools
    assert "plugin_notion_search_pages" in reg._tools
    spec = reg._tools["plugin_notion_search_pages"]
    # schema.name 同步
    assert spec.schema["name"] == "plugin_notion_search_pages"


def test_t4_2_mcp_source_skips_when_already_prefixed():
    """mcp/manager.py 已手动加 mcp_<server>_<tool> 前缀；不应被二次前缀."""
    from deskpet.tools.registry import ToolRegistry

    reg = ToolRegistry()
    reg.register(
        "mcp_localfs_read_file", "mcp",
        {"name": "mcp_localfs_read_file", "description": "x", "parameters": {}},
        lambda a, t: "{}",
        source="mcp:localfs",
        replace_allowed=True,
    )
    # 不应变成 mcp_localfs_mcp_localfs_read_file
    assert "mcp_localfs_read_file" in reg._tools
    assert "mcp_localfs_mcp_localfs_read_file" not in reg._tools


def test_t4_2_builtin_source_no_prefix():
    """source=builtin（默认）不加前缀."""
    from deskpet.tools.registry import ToolRegistry

    reg = ToolRegistry()
    reg.register(
        "file_read", "file",
        {"name": "file_read", "description": "x", "parameters": {}},
        lambda a, t: "{}",
        source="builtin",
    )
    assert "file_read" in reg._tools


def test_t4_2_two_plugins_same_name_no_conflict():
    """**核心 use case**：两个 plugin 注册同名 tool，前缀化后不冲突."""
    from deskpet.tools.registry import ToolRegistry

    reg = ToolRegistry()
    # plugin notion 注册 search
    reg.register(
        "search", "control",
        {"name": "search", "description": "notion search", "parameters": {}},
        lambda a, t: '{"plugin": "notion"}',
        source="plugin:notion",
    )
    # plugin slack 注册同名 search — 不应崩（前缀化后唯一）
    reg.register(
        "search", "control",
        {"name": "search", "description": "slack search", "parameters": {}},
        lambda a, t: '{"plugin": "slack"}',
        source="plugin:slack",
    )
    assert "plugin_notion_search" in reg._tools
    assert "plugin_slack_search" in reg._tools
    # 两边 schema 都不冲突
    assert reg._tools["plugin_notion_search"].schema["description"] == "notion search"
    assert reg._tools["plugin_slack_search"].schema["description"] == "slack search"


# ─── WI-T5.1 子项: load_config cache ────────────────────────


def test_t5_1_cache_returns_same_instance_on_unchanged_mtime(tmp_path):
    """同 path + 同 mtime → 返同一个 AppConfig 实例（cache 命中）."""
    from config import load_config, _reset_load_config_cache

    _reset_load_config_cache()
    cfg_file = tmp_path / "test.toml"
    cfg_file.write_text("[backend]\nport = 9100\n", encoding="utf-8")

    cfg1 = load_config(cfg_file)
    cfg2 = load_config(cfg_file)
    assert cfg1 is cfg2, (
        "WI-T5.1: 同 mtime 应返同一对象（cache hit），实测每次重建 → IO 浪费"
    )


def test_t5_1_cache_invalidated_on_mtime_change(tmp_path):
    """改 toml 后 mtime 变 → cache 失效，重新读."""
    from config import load_config, _reset_load_config_cache

    _reset_load_config_cache()
    cfg_file = tmp_path / "test.toml"
    cfg_file.write_text("[backend]\nport = 9100\n", encoding="utf-8")

    cfg1 = load_config(cfg_file)
    assert cfg1.backend.port == 9100

    # 强制 mtime 推进（同秒内改可能 mtime 一致 — sleep + 显式 set）
    time.sleep(0.05)
    cfg_file.write_text("[backend]\nport = 9200\n", encoding="utf-8")
    # 显式 bump mtime（防文件系统精度问题）
    future = time.time() + 1.0
    os.utime(cfg_file, (future, future))

    cfg2 = load_config(cfg_file)
    assert cfg2 is not cfg1, "mtime 改变后应是新对象"
    assert cfg2.backend.port == 9200


def test_t5_1_cache_invalidated_on_path_change(tmp_path):
    """不同 path → cache 不命中，分别读."""
    from config import load_config, _reset_load_config_cache

    _reset_load_config_cache()
    a = tmp_path / "a.toml"
    a.write_text("[backend]\nport = 9100\n", encoding="utf-8")
    b = tmp_path / "b.toml"
    b.write_text("[backend]\nport = 9200\n", encoding="utf-8")

    cfg_a = load_config(a)
    cfg_b = load_config(b)
    assert cfg_a is not cfg_b
    assert cfg_a.backend.port == 9100
    assert cfg_b.backend.port == 9200


def test_t5_1_missing_file_not_cached(tmp_path):
    """文件不存在时不缓存（每次返新的默认 AppConfig）."""
    from config import load_config, _reset_load_config_cache

    _reset_load_config_cache()
    nonexistent = tmp_path / "ghost.toml"
    cfg1 = load_config(nonexistent)
    cfg2 = load_config(nonexistent)
    # 不缓存 — 是两个不同对象
    assert cfg1 is not cfg2
