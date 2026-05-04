"""P4-S20 Wave 0c: ToolRegistry v2 extension TDD tests.

These verify the new fields, schema methods, permission-gated execution,
and namespacing — without breaking the existing 17 hardcoded tools or
the 679 baseline tests.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from deskpet.tools.registry import ToolRegistry, ToolSpec
from deskpet.permissions.gate import PermissionGate, PermissionGateConfig
from deskpet.types.skill_platform import PermissionResponse


# ---------------------------------------------------------------------
# Backward compat
# ---------------------------------------------------------------------


def test_legacy_register_works() -> None:
    """Existing register() signature with no v2 fields must keep working."""
    reg = ToolRegistry()
    reg.register(
        name="legacy_tool",
        toolset="util",
        schema={
            "name": "legacy_tool",
            "description": "legacy",
            "parameters": {"type": "object", "properties": {}},
        },
        handler=lambda args, tid: '{"ok":true}',
    )
    spec = reg.get("legacy_tool")
    assert spec is not None
    assert spec.permission_category == "read_file"  # safe default
    assert spec.source == "builtin"
    assert spec.dangerous is False
    # Existing dispatch path must still work
    assert reg.dispatch("legacy_tool", {}) == '{"ok":true}'


def test_existing_schemas_method_unchanged() -> None:
    reg = ToolRegistry()
    reg.register(
        "foo",
        "util",
        {"name": "foo", "description": "d", "parameters": {}},
        lambda a, t: "{}",
    )
    out = reg.schemas()
    assert isinstance(out, list) and out
    assert out[0]["type"] == "function"
    assert out[0]["function"]["name"] == "foo"


# ---------------------------------------------------------------------
# v2 extended fields
# ---------------------------------------------------------------------


def test_register_v2_with_extended_fields() -> None:
    reg = ToolRegistry()
    reg.register(
        name="desktop_create_file",
        toolset="os",
        schema={
            "name": "desktop_create_file",
            "description": "Create a file on the desktop",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["name", "content"],
            },
        },
        handler=lambda args, tid: json.dumps(
            {"path": "/desktop/" + args["name"], "platform": "test"}
        ),
        permission_category="desktop_write",
        source="builtin",
        dangerous=False,
    )
    spec = reg.get("desktop_create_file")
    assert spec is not None
    assert spec.permission_category == "desktop_write"
    assert spec.source == "builtin"


# ---------------------------------------------------------------------
# Schema methods
# ---------------------------------------------------------------------


def _seed(reg: ToolRegistry) -> None:
    reg.register(
        "read_file",
        "os",
        {
            "name": "read_file",
            "description": "Read a file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
        lambda a, t: '{"content":""}',
        permission_category="read_file",
    )
    reg.register(
        "run_shell",
        "os",
        {
            "name": "run_shell",
            "description": "Run a shell command",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
        lambda a, t: "{}",
        permission_category="shell",
        dangerous=True,
    )


def test_to_openai_schema_shape() -> None:
    reg = ToolRegistry()
    _seed(reg)
    out = reg.to_openai_schema()
    assert isinstance(out, list) and len(out) == 2
    for item in out:
        assert item["type"] == "function"
        fn = item["function"]
        assert "name" in fn and "description" in fn and "parameters" in fn


def test_to_anthropic_schema_shape() -> None:
    reg = ToolRegistry()
    _seed(reg)
    out = reg.to_anthropic_schema()
    assert isinstance(out, list) and len(out) == 2
    for item in out:
        assert "name" in item
        assert "description" in item
        assert "input_schema" in item
        # Anthropic specifically does NOT use the "parameters" key
        assert "parameters" not in item


def test_to_openai_schema_filter_by_name() -> None:
    reg = ToolRegistry()
    _seed(reg)
    out = reg.to_openai_schema(names=["read_file"])
    assert len(out) == 1
    assert out[0]["function"]["name"] == "read_file"


def test_to_openai_schema_filter_by_category() -> None:
    """Safe-mode: only expose read-only tools to LLM."""
    reg = ToolRegistry()
    _seed(reg)
    out = reg.to_openai_schema(filter_categories=["read_file"])
    names = [item["function"]["name"] for item in out]
    assert "read_file" in names
    assert "run_shell" not in names


# ---------------------------------------------------------------------
# Source filtering
# ---------------------------------------------------------------------


def test_list_tools_by_source() -> None:
    reg = ToolRegistry()
    reg.register(
        "core",
        "util",
        {"name": "core", "description": "c", "parameters": {}},
        lambda a, t: "{}",
        source="builtin",
    )
    reg.register(
        "notion:create_page",
        "plugin",
        {
            "name": "notion:create_page",
            "description": "p",
            "parameters": {},
        },
        lambda a, t: "{}",
        source="plugin:notion",
    )
    plugin_tools = reg.list_tools(source="plugin:notion")
    assert plugin_tools == ["notion:create_page"]
    builtin_tools = reg.list_tools(source="builtin")
    assert builtin_tools == ["core"]


# ---------------------------------------------------------------------
# Permission-gated execute_tool
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_permission_allow() -> None:
    reg = ToolRegistry()
    gate = PermissionGate(
        config=PermissionGateConfig(timeout_s=0.5)
    )

    async def allow(req):
        return PermissionResponse(request_id=req.request_id, decision="allow")

    gate.set_responder(allow)
    reg.set_permission_gate(gate)

    reg.register(
        "write_file",
        "os",
        {"name": "write_file", "description": "w", "parameters": {}},
        lambda a, t: '{"bytes":5}',
        permission_category="write_file",
    )
    out = await reg.execute_tool(
        "write_file", {"path": "x", "content": "hello"}, "s1"
    )
    assert out["ok"] is True
    assert out["result"] == '{"bytes":5}'


@pytest.mark.asyncio
async def test_execute_tool_permission_deny() -> None:
    reg = ToolRegistry()
    gate = PermissionGate(
        config=PermissionGateConfig(timeout_s=0.5)
    )

    async def deny(req):
        return PermissionResponse(request_id=req.request_id, decision="deny")

    gate.set_responder(deny)
    reg.set_permission_gate(gate)

    called = {"n": 0}

    def handler(args: dict[str, Any], tid: str) -> str:
        called["n"] += 1
        return '{"ok":true}'

    reg.register(
        "write_file",
        "os",
        {"name": "write_file", "description": "w", "parameters": {}},
        handler,
        permission_category="write_file",
    )
    out = await reg.execute_tool(
        "write_file", {"path": "x", "content": "hi"}, "s1"
    )
    assert out["ok"] is False
    assert "permission denied" in out["error"]
    assert called["n"] == 0  # handler never ran


@pytest.mark.asyncio
async def test_execute_tool_handler_exception_caught() -> None:
    reg = ToolRegistry()
    gate = PermissionGate(config=PermissionGateConfig(timeout_s=0.5))

    async def allow(req):
        return PermissionResponse(request_id=req.request_id, decision="allow")

    gate.set_responder(allow)
    reg.set_permission_gate(gate)

    def boom(args: dict[str, Any], tid: str) -> str:
        raise OSError("disk full")

    reg.register(
        "write_file",
        "os",
        {"name": "write_file", "description": "w", "parameters": {}},
        boom,
        permission_category="write_file",
    )
    out = await reg.execute_tool(
        "write_file", {"path": "x", "content": "."}, "s1"
    )
    assert out["ok"] is False
    assert "OSError" in out["error"]


@pytest.mark.asyncio
async def test_execute_tool_unknown_returns_error() -> None:
    reg = ToolRegistry()
    out = await reg.execute_tool("nonesuch", {}, "s1")
    assert out["ok"] is False
    assert "unknown" in out["error"].lower()


# ---------------------------------------------------------------------
# Namespace conflict
# ---------------------------------------------------------------------


def test_register_replace_logs_warning(caplog) -> None:
    """Existing behavior: re-register replaces with warning (kept for compat)."""
    reg = ToolRegistry()
    reg.register(
        "foo", "util",
        {"name": "foo", "description": "d", "parameters": {}},
        lambda a, t: "{}",
    )
    with caplog.at_level("WARNING"):
        reg.register(
            "foo", "util",
            {"name": "foo", "description": "d2", "parameters": {}},
            lambda a, t: "{}",
        )
    assert any("re-registered" in r.message for r in caplog.records)
