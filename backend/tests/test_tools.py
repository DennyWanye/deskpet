"""Tests for Tool protocol, ToolRegistry, and built-in tools."""
from __future__ import annotations

from datetime import datetime

import pytest

from tools.base import Tool, ToolSpec
from tools.get_time import GetTimeTool, get_time_tool
from tools.registry import ToolRegistry


def test_tool_spec_is_frozen():
    spec = ToolSpec(name="x", description="y")
    with pytest.raises((AttributeError, Exception)):
        spec.name = "z"  # type: ignore[misc]


def test_get_time_satisfies_tool_protocol():
    assert isinstance(get_time_tool, Tool)
    assert get_time_tool.spec.name == "get_time"


@pytest.mark.asyncio
async def test_get_time_returns_valid_iso():
    result = await get_time_tool.invoke()
    # Parses cleanly back
    parsed = datetime.fromisoformat(result)
    # Sanity: within a minute of "now"
    assert abs((datetime.now() - parsed).total_seconds()) < 60


def test_registry_register_and_get():
    reg = ToolRegistry()
    reg.register(get_time_tool)
    assert reg.get("get_time") is get_time_tool
    assert reg.get("nonexistent") is None


def test_registry_rejects_duplicate():
    reg = ToolRegistry()
    reg.register(get_time_tool)
    with pytest.raises(ValueError, match="already registered"):
        reg.register(get_time_tool)


def test_registry_list_specs():
    reg = ToolRegistry()
    assert reg.list_specs() == []
    reg.register(get_time_tool)
    specs = reg.list_specs()
    assert len(specs) == 1
    assert specs[0].name == "get_time"


def test_registry_prompt_hint_empty_when_no_tools():
    reg = ToolRegistry()
    hint = reg.prompt_hint()
    assert hint == ""


def test_registry_prompt_hint_lists_tools():
    reg = ToolRegistry()
    reg.register(get_time_tool)
    hint = reg.prompt_hint()
    assert "get_time" in hint
    assert "<tool>" in hint  # protocol instruction present
    assert "Returns the current local date" in hint
