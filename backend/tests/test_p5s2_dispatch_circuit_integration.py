# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P5-S2 Phase 3.2: ToolRegistry.execute_tool integrates the breaker.

* Breaker OPEN → dispatch refuses to call the handler and returns a
  synthetic envelope with ``error="circuit_open"``, a hint, and a list
  of alternatives drawn from the same toolset.
* Every dispatch outcome (handler ok / not ok / raised) is recorded
  to the breaker so it can transition.
"""
from __future__ import annotations

import json

import pytest

from agent.circuit_breaker import ToolCircuitBreaker
from deskpet.tools.registry import ToolRegistry


# ─────────────── helpers ───────────────


def _make_registry_with_breaker(
    breaker: ToolCircuitBreaker | None = None,
) -> ToolRegistry:
    """Fresh registry wired to a breaker. Tests don't pollute the
    global ``registry`` singleton."""
    reg = ToolRegistry()
    if breaker is not None:
        reg.set_circuit_breaker(breaker)
    return reg


def _ok_handler(name: str = "ok") -> object:
    """Returns a registry handler that always returns ``{"ok": true}``
    JSON. Tracks call count via mutable closure."""
    counter = {"calls": 0}

    def handler(params, task_id):  # noqa: ANN001 — registry signature
        counter["calls"] += 1
        return json.dumps({"ok": True, "result": "did the thing"})

    handler.counter = counter  # type: ignore[attr-defined]
    return handler


def _raises_handler() -> object:
    counter = {"calls": 0}

    def handler(params, task_id):  # noqa: ANN001
        counter["calls"] += 1
        raise ValueError("bad arg")

    handler.counter = counter  # type: ignore[attr-defined]
    return handler


def _fail_envelope_handler() -> object:
    """Handler that returns a structured failure envelope (NOT raises).

    This is the realistic Phase 0 sensor pattern (e.g. write_file
    returns ``{"ok": false, "error": "missing required parameter: path"}``).
    The breaker should still record this as a failure even though the
    Python call didn't raise."""
    counter = {"calls": 0}

    def handler(params, task_id):  # noqa: ANN001
        counter["calls"] += 1
        return json.dumps({"ok": False, "error": "would_overwrite"})

    handler.counter = counter  # type: ignore[attr-defined]
    return handler


def _trivial_schema(name: str) -> dict:
    return {
        "name": name,
        "description": "test",
        "parameters": {"type": "object", "properties": {}},
    }


# ─────────────── tests ───────────────


@pytest.mark.asyncio
async def test_dispatch_blocked_when_open() -> None:
    """Pre-OPEN the breaker, then call execute_tool. Handler must NOT
    fire and the envelope must contain the synthetic ``circuit_open``
    error + hint + alternatives."""
    breaker = ToolCircuitBreaker(threshold=3, cooldown_seconds=60.0)
    # Pre-open by recording 3 failures.
    for _ in range(3):
        await breaker.record_call("sX", "write_file", ok=False)
    assert await breaker.state("sX", "write_file") == "OPEN"

    reg = _make_registry_with_breaker(breaker)
    handler = _ok_handler()
    reg.register("write_file", "file", _trivial_schema("write_file"), handler)
    # Sibling tool in same toolset — should appear in alternatives.
    reg.register("edit_file", "file", _trivial_schema("edit_file"), _ok_handler())
    # Different toolset — should NOT appear.
    reg.register("run_shell", "shell", _trivial_schema("run_shell"), _ok_handler())

    envelope = await reg.execute_tool(
        "write_file", {"path": "/tmp/x"}, session_id="sX", task_id="t1"
    )

    # Handler MUST NOT have been called.
    assert handler.counter["calls"] == 0, "handler ran despite OPEN breaker"

    # Envelope shape: top-level ok=False, structured circuit_open payload
    # parsed out of the result string.
    assert envelope["ok"] is False
    assert envelope["error"] is not None
    payload = json.loads(envelope["result"])
    assert payload["ok"] is False
    assert payload["error"] == "circuit_open"
    assert "write_file" in payload["hint"]
    assert "circuit" in payload["hint"].lower() or "熔断" in payload["hint"]
    # alternatives MUST include the sibling tool, NOT the cross-toolset one.
    alts = payload["available_alternatives"]
    assert "edit_file" in alts
    assert "run_shell" not in alts
    # And it must NOT include itself.
    assert "write_file" not in alts


@pytest.mark.asyncio
async def test_dispatch_records_outcome_success() -> None:
    """Real dispatch returning ok → breaker records success (counter
    stays at 0)."""
    breaker = ToolCircuitBreaker(threshold=3, cooldown_seconds=60.0)
    reg = _make_registry_with_breaker(breaker)
    reg.register("write_file", "file", _trivial_schema("write_file"), _ok_handler())

    envelope = await reg.execute_tool(
        "write_file", {}, session_id="sY", task_id="t1"
    )
    assert envelope["ok"] is True

    # No failure recorded.
    assert await breaker.state("sY", "write_file") == "CLOSED"


@pytest.mark.asyncio
async def test_dispatch_records_outcome_failure_envelope() -> None:
    """Handler returns ``{"ok": false, ...}`` → breaker records failure.
    3 of these in a row → breaker OPEN."""
    breaker = ToolCircuitBreaker(threshold=3, cooldown_seconds=60.0)
    reg = _make_registry_with_breaker(breaker)
    reg.register(
        "write_file", "file", _trivial_schema("write_file"), _fail_envelope_handler()
    )

    for _ in range(3):
        env = await reg.execute_tool(
            "write_file", {}, session_id="sZ", task_id="t"
        )
        # The dispatch envelope is "ok" at the registry level (handler
        # returned a string, no exception). The failure is *inside* the
        # nested result. The breaker must look at that nested layer.
        assert env["ok"] is True

    # 3 failures recorded → OPEN.
    assert await breaker.state("sZ", "write_file") == "OPEN"

    # 4th call — handler should NOT fire.
    env4 = await reg.execute_tool(
        "write_file", {}, session_id="sZ", task_id="t"
    )
    payload = json.loads(env4["result"])
    assert payload["error"] == "circuit_open"


@pytest.mark.asyncio
async def test_dispatch_records_outcome_handler_raised() -> None:
    """Handler raises → registry returns error envelope AND breaker
    records failure."""
    breaker = ToolCircuitBreaker(threshold=3, cooldown_seconds=60.0)
    reg = _make_registry_with_breaker(breaker)
    handler = _raises_handler()
    reg.register("write_file", "file", _trivial_schema("write_file"), handler)

    for _ in range(3):
        env = await reg.execute_tool(
            "write_file", {}, session_id="sR", task_id="t"
        )
        assert env["ok"] is False  # exception → ok=False at envelope level

    assert handler.counter["calls"] == 3
    assert await breaker.state("sR", "write_file") == "OPEN"


@pytest.mark.asyncio
async def test_dispatch_with_no_breaker_works_unchanged() -> None:
    """Backward compat: registry without a breaker (legacy code path)
    still works and never references the missing breaker."""
    reg = _make_registry_with_breaker(None)  # no breaker attached
    reg.register("read_file", "file", _trivial_schema("read_file"), _ok_handler())

    env = await reg.execute_tool(
        "read_file", {}, session_id="s", task_id="t"
    )
    assert env["ok"] is True
