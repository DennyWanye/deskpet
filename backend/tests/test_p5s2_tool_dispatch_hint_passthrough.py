"""P5-S2 Phase 0 task 0.9 — registry execute_tool must not eat hint field.

Spec: openspec/changes/p5-s2-self-healing-harness/specs/tool-registry/sensor-feedback.md

The new error envelope is ``{"ok": false, "error": "...", "hint": "...",
"examples": [...]}``. ``ToolRegistry.execute_tool`` returns its own
envelope ``{"ok": bool, "result": str | None, "error": str | None}``
where ``result`` is the handler's JSON string.

The contract: when the handler embeds a ``hint`` in its JSON-encoded
result, the dispatcher MUST NOT strip / lose / overwrite that hint —
it round-trips through ``result`` to the LLM consumer.

(``registry_v2`` in the spec refers to the same file
``deskpet/tools/registry.py`` — its ``execute_tool`` async method
is the v2 dispatch path; ``registry_v2.py`` was never split out.)
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from deskpet.tools.registry import ToolRegistry


def _make_registry_with_hinted_handler() -> ToolRegistry:
    reg = ToolRegistry()

    def handler(args: dict[str, Any], task_id: str) -> str:
        return json.dumps(
            {
                "ok": False,
                "error": "x",
                "hint": "do y",
                "examples": [{"path": "ok.txt", "content": "hi"}],
            },
            ensure_ascii=False,
        )

    reg.register(
        name="hinted_tool",
        toolset="test",
        schema={
            "name": "hinted_tool",
            "description": "test",
            "parameters": {"type": "object", "properties": {}},
        },
        handler=handler,
    )
    return reg


def test_execute_tool_preserves_hint_in_result() -> None:
    reg = _make_registry_with_hinted_handler()
    out = asyncio.new_event_loop().run_until_complete(
        reg.execute_tool("hinted_tool", {}, session_id="sid-1")
    )
    # Outer envelope: ok=True (handler ran without raising), result is
    # the handler's JSON string. The handler-level error lives inside.
    assert out["ok"] is True, f"outer envelope: {out!r}"
    assert out["result"] is not None
    payload = json.loads(out["result"])
    assert payload["ok"] is False
    assert payload["error"] == "x"
    assert payload["hint"] == "do y", (
        f"hint field was eaten by dispatch! payload={payload!r}"
    )
    assert payload["examples"] == [{"path": "ok.txt", "content": "hi"}]


def test_dispatch_sync_preserves_hint() -> None:
    """Same check via the legacy sync ``dispatch()`` path."""
    reg = _make_registry_with_hinted_handler()
    raw = reg.dispatch("hinted_tool", {}, "")
    payload = json.loads(raw)
    assert payload["hint"] == "do y", (
        f"sync dispatch lost hint: {payload!r}"
    )
    assert payload["examples"] == [{"path": "ok.txt", "content": "hi"}]
