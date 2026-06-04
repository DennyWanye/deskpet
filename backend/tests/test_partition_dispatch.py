# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""G3 (companion-code-v2) — ToolRegistry.partition_dispatch unit tests.

Coverage:
  * ToolSpec.concurrency_safe default is True (BC)
  * register(..., concurrency_safe=False) wires through
  * partition: all-safe batch runs concurrently
  * partition: all-unsafe batch runs serially in input order
  * partition: mixed batch — result order matches input order
  * partition: empty list returns []
  * partition: unknown tool name treated as safe (no batch drag)
  * partition: tuple / dict / object input shapes all accepted
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import namedtuple
from typing import Any

import pytest

from deskpet.tools.registry import ToolRegistry, ToolSpec


ToolCall = namedtuple("ToolCall", ["name", "args", "task_id"])


def _make_handler(
    *,
    name: str,
    sleep_s: float = 0.0,
    started_log: list[tuple[str, float]] | None = None,
) -> Any:
    """Sync handler that logs its start timestamp + name so tests can
    assert concurrency / ordering. Returns a JSON envelope string.
    """

    def _h(args: dict[str, Any], task_id: str) -> str:
        if started_log is not None:
            started_log.append((name, time.monotonic()))
        if sleep_s:
            time.sleep(sleep_s)
        return json.dumps({"ok": True, "tool": name, "args": args})

    return _h


@pytest.fixture
def reg() -> ToolRegistry:
    """Fresh registry per test — never pollute the module-level singleton."""
    return ToolRegistry()


def _schema(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": f"test tool {name}",
        "parameters": {"type": "object", "properties": {}},
    }


# ---------------------------------------------------------------------------
# Spec field + registration plumbing
# ---------------------------------------------------------------------------


def test_toolspec_default_concurrency_safe_true(reg: ToolRegistry) -> None:
    """Backward compat: tools registered without the kwarg are safe."""
    reg.register("read_a", "test", _schema("read_a"), _make_handler(name="read_a"))
    spec = reg.get("read_a")
    assert spec is not None
    assert spec.concurrency_safe is True


def test_register_concurrency_safe_false_propagates(reg: ToolRegistry) -> None:
    """register(..., concurrency_safe=False) → ToolSpec.concurrency_safe=False."""
    reg.register(
        "write_a", "test", _schema("write_a"), _make_handler(name="write_a"),
        concurrency_safe=False,
    )
    spec = reg.get("write_a")
    assert spec is not None
    assert spec.concurrency_safe is False


def test_is_concurrency_safe_unknown_returns_true(reg: ToolRegistry) -> None:
    """Unknown tool defaults to safe — execute_tool will fail loudly anyway,
    and treating unknowns as safe avoids dragging a whole batch into serial
    mode because of one typo."""
    assert reg._is_concurrency_safe("does_not_exist") is True


# ---------------------------------------------------------------------------
# partition_dispatch behaviour
# ---------------------------------------------------------------------------


def test_partition_empty_returns_empty(reg: ToolRegistry) -> None:
    """No calls → no work → [] (no event loop overhead, no exceptions)."""
    out = asyncio.run(reg.partition_dispatch([], session_id="s"))
    assert out == []


def test_partition_all_safe_runs_concurrently(reg: ToolRegistry) -> None:
    """4 safe handlers each sleep 100ms → wall time well under 400ms because
    they run via asyncio.gather + run_in_executor."""
    log: list[tuple[str, float]] = []
    for n in ("a", "b", "c", "d"):
        reg.register(n, "test", _schema(n), _make_handler(name=n, sleep_s=0.1, started_log=log))
    calls = [ToolCall(n, {"x": i}, str(i)) for i, n in enumerate(("a", "b", "c", "d"))]

    t0 = time.monotonic()
    results = asyncio.run(reg.partition_dispatch(calls, session_id="s"))
    elapsed = time.monotonic() - t0

    # Concurrency assertion: all 4 started within 200ms of each other
    starts = sorted(t for _, t in log)
    assert len(starts) == 4
    assert (starts[-1] - starts[0]) < 0.2, f"safe batch did not run concurrently (spread={starts[-1]-starts[0]:.3f}s)"
    # Total wall well under 4 * 100ms (allow generous slack for executor)
    assert elapsed < 0.35, f"safe batch took {elapsed:.3f}s — should be ~0.1s concurrent"
    # All envelopes ok
    assert all(r["ok"] is True for r in results)
    # Order preserved (a, b, c, d)
    payloads = [json.loads(r["result"]) for r in results]
    assert [p["tool"] for p in payloads] == ["a", "b", "c", "d"]


def test_partition_all_unsafe_runs_serially(reg: ToolRegistry) -> None:
    """3 unsafe handlers each sleep 80ms → wall time ≈ 3 * 80ms (serial)."""
    log: list[tuple[str, float]] = []
    for n in ("w1", "w2", "w3"):
        reg.register(
            n, "test", _schema(n),
            _make_handler(name=n, sleep_s=0.08, started_log=log),
            concurrency_safe=False,
        )
    calls = [ToolCall(n, {}, "") for n in ("w1", "w2", "w3")]

    t0 = time.monotonic()
    results = asyncio.run(reg.partition_dispatch(calls, session_id="s"))
    elapsed = time.monotonic() - t0

    # Wall time should be roughly 3 * 80ms = 240ms+ (allow slack).
    # If they ran concurrently it'd finish < 150ms.
    assert elapsed >= 0.18, f"unsafe batch ran concurrently? elapsed={elapsed:.3f}s"
    # Starts strictly increasing → serial execution
    starts = [t for _, t in log]
    assert starts == sorted(starts), "unsafe batch start times not monotonically increasing"
    # Each subsequent start happens AFTER previous handler had time to enter sleep
    for i in range(1, len(starts)):
        assert starts[i] - starts[i - 1] >= 0.05, (
            f"unsafe batch started call {i} only {starts[i]-starts[i-1]:.3f}s after call {i-1} — not serial"
        )
    # Result order matches input order
    payloads = [json.loads(r["result"]) for r in results]
    assert [p["tool"] for p in payloads] == ["w1", "w2", "w3"]


def test_partition_mixed_preserves_input_order(reg: ToolRegistry) -> None:
    """Mixed safe/unsafe batch: results[i] must correspond to calls[i],
    not to the safe-first/unsafe-second internal split order."""
    reg.register("s1", "test", _schema("s1"), _make_handler(name="s1"))
    reg.register(
        "u1", "test", _schema("u1"), _make_handler(name="u1"),
        concurrency_safe=False,
    )
    reg.register("s2", "test", _schema("s2"), _make_handler(name="s2"))
    reg.register(
        "u2", "test", _schema("u2"), _make_handler(name="u2"),
        concurrency_safe=False,
    )
    calls = [
        ToolCall("u1", {"k": "first"}, ""),    # unsafe first
        ToolCall("s1", {"k": "second"}, ""),   # safe second
        ToolCall("u2", {"k": "third"}, ""),    # unsafe third
        ToolCall("s2", {"k": "fourth"}, ""),   # safe fourth
    ]
    results = asyncio.run(reg.partition_dispatch(calls, session_id="s"))
    assert len(results) == 4
    payloads = [json.loads(r["result"]) for r in results]
    # Even though partition reorders execution, output must match input slot.
    assert payloads[0]["tool"] == "u1"
    assert payloads[1]["tool"] == "s1"
    assert payloads[2]["tool"] == "u2"
    assert payloads[3]["tool"] == "s2"
    # Each args round-tripped correctly into its slot
    assert payloads[0]["args"] == {"k": "first"}
    assert payloads[3]["args"] == {"k": "fourth"}


def test_partition_accepts_tuple_dict_object_inputs(reg: ToolRegistry) -> None:
    """Duck-typed inputs: namedtuple / tuple / dict / object with attrs
    all extract (name, args, task_id) correctly."""
    reg.register("t1", "test", _schema("t1"), _make_handler(name="t1"))
    reg.register("t2", "test", _schema("t2"), _make_handler(name="t2"))
    reg.register("t3", "test", _schema("t3"), _make_handler(name="t3"))
    reg.register("t4", "test", _schema("t4"), _make_handler(name="t4"))

    class _Obj:
        def __init__(self, name: str, args: dict[str, Any], task_id: str = "") -> None:
            self.name = name
            self.args = args
            self.task_id = task_id

    calls = [
        ToolCall("t1", {"v": 1}, "id1"),              # namedtuple
        ("t2", {"v": 2}, "id2"),                       # bare tuple
        {"name": "t3", "args": {"v": 3}, "task_id": "id3"},  # dict
        _Obj("t4", {"v": 4}, "id4"),                   # generic object
    ]
    results = asyncio.run(reg.partition_dispatch(calls, session_id="s"))
    payloads = [json.loads(r["result"]) for r in results]
    assert [p["tool"] for p in payloads] == ["t1", "t2", "t3", "t4"]
    assert [p["args"]["v"] for p in payloads] == [1, 2, 3, 4]


def test_partition_unknown_tool_isolated(reg: ToolRegistry) -> None:
    """Unknown tool in a batch returns its own error envelope; siblings
    still succeed. Output order preserved."""
    reg.register("good", "test", _schema("good"), _make_handler(name="good"))
    calls = [
        ToolCall("good", {}, ""),
        ToolCall("ghost", {}, ""),  # not registered
        ToolCall("good", {}, ""),
    ]
    results = asyncio.run(reg.partition_dispatch(calls, session_id="s"))
    assert len(results) == 3
    assert results[0]["ok"] is True
    assert results[1]["ok"] is False
    assert "unknown tool" in (results[1]["error"] or "")
    assert results[2]["ok"] is True


def test_partition_unsafe_failure_does_not_block_remaining_unsafe(
    reg: ToolRegistry,
) -> None:
    """A failing unsafe handler returns an error envelope but subsequent
    unsafe handlers still execute (the loop catches per-call inside
    execute_tool — partition_dispatch never short-circuits)."""

    def _boom(args: dict[str, Any], task_id: str) -> str:
        raise RuntimeError("intentional")

    reg.register("ok_w", "test", _schema("ok_w"), _make_handler(name="ok_w"), concurrency_safe=False)
    reg.register("boom_w", "test", _schema("boom_w"), _boom, concurrency_safe=False)
    reg.register("after_w", "test", _schema("after_w"), _make_handler(name="after_w"), concurrency_safe=False)

    calls = [
        ToolCall("ok_w", {}, ""),
        ToolCall("boom_w", {}, ""),
        ToolCall("after_w", {}, ""),
    ]
    results = asyncio.run(reg.partition_dispatch(calls, session_id="s"))
    assert results[0]["ok"] is True
    assert results[1]["ok"] is False
    assert "RuntimeError" in (results[1]["error"] or "")
    assert results[2]["ok"] is True


# ---------------------------------------------------------------------------
# Cross-check: 10+ real write tools are marked unsafe
# ---------------------------------------------------------------------------


def test_real_write_tools_marked_unsafe() -> None:
    """Smoke-check the actual registry singleton: every known write tool
    that was tagged in this change ships with concurrency_safe=False.

    If you add a new write tool, register it with concurrency_safe=False
    AND add its name here. If a tool isn't loaded in the test environment
    (e.g. office tools missing python-pptx), it's skipped — not failed —
    so the test runs in CI without optional deps.
    """
    # Trigger tool discovery
    import deskpet.tools  # noqa: F401
    from deskpet.tools.registry import registry as global_registry

    expected_unsafe = [
        "file_write",
        "memory_write",
        "memory_forget",
        # Optional / env-gated tools — checked only if present:
        "excel_create", "ppt_create", "doc_create", "doc_edit",
        "file_organize", "write_file", "edit_file", "run_shell",
        "desktop_create_file",
    ]
    checked = 0
    for name in expected_unsafe:
        spec = global_registry.get(name)
        if spec is None:
            continue  # not loaded in this test env — skip
        assert spec.concurrency_safe is False, (
            f"write tool {name!r} should be concurrency_safe=False; got True"
        )
        checked += 1
    # Hard lower bound: at least core writes must always be present.
    # file_write / memory_write / memory_forget are unconditional.
    assert checked >= 3, (
        f"expected ≥3 known write tools in registry, only checked {checked} "
        f"({expected_unsafe!r})"
    )
