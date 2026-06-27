# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Tool visibility gating (goal-tool mis-fire fix).

Covers the ``ToolSpec.visible_when`` predicate added so goal_task_* tools
stay hidden from the LLM prompt until a /goal is active. The handler's
"请先 /goal" guard is unchanged; this layer only controls *schema* exposure.
"""
from __future__ import annotations

from deskpet.tools.registry import ToolRegistry
from deskpet.agent.goal_store import SessionGoalStore
from deskpet.agent.task_graph import TaskGraphStore
from deskpet.memory.session_db import SessionDB
from deskpet.memory.memory_v2_schema import _reset_cache_for_tests
from deskpet.tools.task_graph_tools import build_global_goal_task_tools

_SCHEMA = {"name": "demo_tool", "description": "x", "parameters": {"type": "object", "properties": {}}}


async def _noop(args, corr_id=""):  # pragma: no cover - never dispatched here
    return "{}"


def _names(schemas):
    out = []
    for s in schemas:
        fn = s.get("function", s)
        out.append(fn.get("name"))
    return out


def test_no_predicate_is_always_visible():
    """Default (visible_when=None) → tool always in schemas (BC)."""
    reg = ToolRegistry()
    reg.register(name="demo_tool", toolset="t", schema=_SCHEMA, handler=_noop)
    assert "demo_tool" in _names(reg.schemas())


def test_predicate_false_hides_tool():
    reg = ToolRegistry()
    reg.register(
        name="demo_tool", toolset="t", schema=_SCHEMA, handler=_noop,
        visible_when=lambda: False,
    )
    assert "demo_tool" not in _names(reg.schemas())


def test_predicate_true_shows_tool():
    reg = ToolRegistry()
    reg.register(
        name="demo_tool", toolset="t", schema=_SCHEMA, handler=_noop,
        visible_when=lambda: True,
    )
    assert "demo_tool" in _names(reg.schemas())


def test_predicate_reevaluated_per_call():
    """Mirrors the goal flow: hidden with no active goal, shown once set."""
    active = {"goal": False}
    reg = ToolRegistry()
    reg.register(
        name="goal_task_create", toolset="goal", schema={**_SCHEMA, "name": "goal_task_create"},
        handler=_noop, visible_when=lambda: active["goal"],
    )
    assert "goal_task_create" not in _names(reg.schemas())  # normal chat
    active["goal"] = True                                    # user runs /goal
    assert "goal_task_create" in _names(reg.schemas())
    active["goal"] = False                                   # goal cleared
    assert "goal_task_create" not in _names(reg.schemas())


def test_predicate_raises_is_fail_closed():
    """A raising predicate hides the tool rather than exposing it."""
    def _boom():
        raise RuntimeError("store down")

    reg = ToolRegistry()
    reg.register(
        name="demo_tool", toolset="t", schema=_SCHEMA, handler=_noop,
        visible_when=_boom,
    )
    assert "demo_tool" not in _names(reg.schemas())


def test_goal_tools_hidden_until_goal_active_integration():
    """End-to-end mirror of main.py's goal-tool wiring (the real fix).

    With no active goal the 4 goal_task_* tools must NOT appear in the LLM
    schema list; after the user sets a /goal they appear; clearing hides
    them again.
    """
    _reset_cache_for_tests()
    db = SessionDB(":memory:")
    tg = TaskGraphStore(db=db)
    gs = SessionGoalStore()
    gs.bind_persistence(db)

    reg = ToolRegistry()
    visible = lambda: gs.get_active_goal_context() is not None
    for name, schema, handler in build_global_goal_task_tools(
        task_graph_store=tg, goal_resolver=lambda: gs.get_active_goal_context()
    ):
        reg.register(
            name=name, toolset="goal", schema=schema, handler=handler,
            concurrency_safe=name in ("goal_task_list", "goal_task_get"),
            replace_allowed=True, visible_when=visible,
        )

    def _goal_tool_names():
        return sorted(
            n for n in _names(reg.schemas()) if n and n.startswith("goal_task_")
        )

    assert _goal_tool_names() == []           # normal chat → hidden
    gs.set(session_id="sess-1", text="ship the feature")
    assert len(_goal_tool_names()) == 4       # after /goal → visible
    gs.clear("sess-1")
    assert _goal_tool_names() == []           # goal cleared → hidden again
    _reset_cache_for_tests()
