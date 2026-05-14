"""P6 Phase 1 — TerminationGate state machine unit tests.

TDD-ordered per openspec/changes/p6-agent-loop-refactor/tasks.md Phase 1.
Tests are grouped by sub-phase (1.1 skeleton, 1.2 hard caps, 1.3 per-tool
counter, 1.4 state progression).
"""
from __future__ import annotations

import pytest

from agent.termination import (
    GateConfig,
    GateState,
    TerminationGate,
    TerminationReason,
)


# ---------------------------------------------------------------------------
# 1.1 Data structures + skeleton
# ---------------------------------------------------------------------------


def test_default_config_values() -> None:
    """1.1 GateConfig defaults match design.md."""
    cfg = GateConfig()
    # P6 bugfix 2026-05-14 (final user feedback): "auto-mode 下长任务只要
    # 没卡死就该一直跑下去"。time/count cap 改成"实质 disabled"——只靠
    # args-aware per_tool_max_consecutive 检测真死循环。
    # 历史: 50→200→10000 turns; 40→200→10000 tools; 600→1800→None wall.
    assert cfg.max_turns == 10000
    assert cfg.tool_budget_hard == 10000
    assert cfg.wall_clock_seconds is None
    # Bumped 5 → 8 after live-test bugfix 2026-05-13 (args-aware counter
    # makes 5 too tight; 8 leaves comfortable headroom).
    assert cfg.per_tool_max_consecutive == 8
    assert cfg.max_budget_usd is None


def test_gate_starts_in_running_state() -> None:
    """1.2 Fresh gate.allows_call() returns (True, None)."""
    gate = TerminationGate()
    ok, reason = gate.allows_call()
    assert ok is True
    assert reason is None


def test_terminate_is_idempotent() -> None:
    """1.3 terminate(SUCCESS) then terminate(HARD_MAX_TURNS) keeps SUCCESS."""
    gate = TerminationGate()
    gate.terminate(TerminationReason.SUCCESS)
    gate.terminate(TerminationReason.HARD_MAX_TURNS)
    assert gate.state.terminated is True
    assert gate.state.terminated_reason is TerminationReason.SUCCESS


def test_summary_includes_all_fields() -> None:
    """1.4 summary() has reason, turns_used, tools_used, elapsed_seconds, cost_usd."""
    gate = TerminationGate()
    summary = gate.summary()
    for key in ("reason", "turns_used", "tools_used", "elapsed_seconds", "cost_usd"):
        assert key in summary, f"missing key: {key}"
    # Running gate: reason field is the string 'running'
    assert summary["reason"] == "running"
    assert summary["turns_used"] == 0
    assert summary["tools_used"] == 0
    assert isinstance(summary["elapsed_seconds"], float)
    assert summary["cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# 1.2 Hard cap decision logic
# ---------------------------------------------------------------------------


def test_blocks_when_max_turns_reached() -> None:
    """1.6 record_turn × max_turns -> allows_call = (False, HARD_MAX_TURNS)."""
    gate = TerminationGate(GateConfig(max_turns=50))
    for _ in range(50):
        gate.record_turn()
    ok, reason = gate.allows_call()
    assert ok is False
    assert reason is TerminationReason.HARD_MAX_TURNS


def test_blocks_when_tool_budget_exhausted() -> None:
    """1.7 record_tool_call × tool_budget_hard -> allows_tool blocks."""
    gate = TerminationGate(GateConfig(tool_budget_hard=40))
    for _ in range(40):
        gate.record_tool_call("x")
    ok, reason = gate.allows_tool("x")
    assert ok is False
    assert reason is TerminationReason.HARD_TOOL_BUDGET


def test_blocks_when_wall_clock_exceeded() -> None:
    """1.8 Fake clock past wall_clock_seconds -> HARD_WALL_CLOCK."""
    # Fake clock: starts at t=1000, then jumps to 1601 (601s elapsed)
    clock_values = iter([1000.0, 1601.0])

    def fake_clock() -> float:
        return next(clock_values)

    gate = TerminationGate(
        GateConfig(wall_clock_seconds=600.0),
        clock=fake_clock,
    )
    # first call to fake_clock consumed by GateState.started_at via __init__
    ok, reason = gate.allows_call()
    assert ok is False
    assert reason is TerminationReason.HARD_WALL_CLOCK


def test_blocks_when_max_budget_usd_exceeded() -> None:
    """1.9 cost_usd accumulates past max_budget_usd -> HARD_MAX_BUDGET_USD."""
    gate = TerminationGate(GateConfig(max_budget_usd=1.0))
    gate.record_turn(cost_delta_usd=0.6)
    gate.record_turn(cost_delta_usd=0.6)
    ok, reason = gate.allows_call()
    assert ok is False
    assert reason is TerminationReason.HARD_MAX_BUDGET_USD


def test_after_terminate_all_allows_return_terminated_reason() -> None:
    """1.10 terminate(PERMANENT_TOOL_ERROR) -> allows_call/allows_tool both blocked."""
    gate = TerminationGate()
    gate.terminate(TerminationReason.PERMANENT_TOOL_ERROR)

    ok_call, reason_call = gate.allows_call()
    assert ok_call is False
    assert reason_call is TerminationReason.PERMANENT_TOOL_ERROR

    ok_tool, reason_tool = gate.allows_tool("anything")
    assert ok_tool is False
    assert reason_tool is TerminationReason.PERMANENT_TOOL_ERROR


# ---------------------------------------------------------------------------
# 1.3 Per-tool consecutive counter
# ---------------------------------------------------------------------------


def test_per_tool_consecutive_increments() -> None:
    """1.12 record_tool_call('read_file') × 3 -> per_tool_consecutive['read_file'] == 3."""
    gate = TerminationGate()
    for _ in range(3):
        gate.record_tool_call("read_file")
    assert gate.state.per_tool_consecutive["read_file"] == 3


def test_different_tool_resets_consecutive() -> None:
    """1.13 Different tool resets previous tool's counter."""
    gate = TerminationGate()
    for _ in range(3):
        gate.record_tool_call("read_file")
    gate.record_tool_call("grep")
    assert gate.state.per_tool_consecutive["read_file"] == 0
    assert gate.state.per_tool_consecutive["grep"] == 1


def test_per_tool_blocks_at_threshold() -> None:
    """1.14 record_tool_call('write_file') × 5 -> allows_tool('write_file') blocked.

    Per the task note: 4 calls allowed, after 5th record allows_tool returns blocked.
    """
    gate = TerminationGate(GateConfig(per_tool_max_consecutive=5))
    # Calls 1..4 — allows_tool must return True before each record
    for i in range(4):
        ok, _ = gate.allows_tool("write_file")
        assert ok, f"call {i+1} should be allowed"
        gate.record_tool_call("write_file")
    # 5th: allowed (consec=4 < 5), then we record, taking consec to 5.
    ok, _ = gate.allows_tool("write_file")
    assert ok is True
    gate.record_tool_call("write_file")
    # Now consec == 5, next allows_tool blocks.
    assert gate.state.per_tool_consecutive["write_file"] == 5
    ok, reason = gate.allows_tool("write_file")
    assert ok is False
    assert reason is TerminationReason.HALLUCINATION_DETECTED


# ---------------------------------------------------------------------------
# 1.3b — Args-aware per-tool counter (P6 bugfix 2026-05-13)
# ---------------------------------------------------------------------------


def test_per_tool_consecutive_args_aware_different_args_resets() -> None:
    """P6 bugfix 2026-05-13 (live-test regression): same tool with DIFFERENT
    args does NOT count as consecutive — it's legitimate exploration.

    Pre-fix bug: 5 reads of 5 different files triggered HALLUCINATION_
    DETECTED because the counter ignored args. Now the counter resets to 1
    when args differ.
    """
    gate = TerminationGate(GateConfig(per_tool_max_consecutive=3))
    for i in range(5):
        ok, _ = gate.allows_tool("read_file")
        assert ok, f"different-path read {i+1} should be allowed"
        gate.record_tool_call("read_file", args={"path": f"/tmp/file{i}.txt"})
    # Counter must NEVER cross 1 — each call has unique args
    assert gate.state.per_tool_consecutive["read_file"] == 1
    # And still allowed for next round
    ok, _ = gate.allows_tool("read_file")
    assert ok is True


def test_per_tool_consecutive_args_aware_same_args_still_blocks() -> None:
    """Real death loop (same tool + same args repeated) still triggers
    HALLUCINATION_DETECTED — this is the actual symptom the cap protects.
    """
    gate = TerminationGate(GateConfig(per_tool_max_consecutive=3))
    same_args = {"path": "/tmp/stuck.txt"}
    for _ in range(3):
        ok, _ = gate.allows_tool("read_file")
        assert ok
        gate.record_tool_call("read_file", args=same_args)
    assert gate.state.per_tool_consecutive["read_file"] == 3
    ok, reason = gate.allows_tool("read_file")
    assert ok is False
    assert reason is TerminationReason.HALLUCINATION_DETECTED


def test_per_tool_consecutive_none_args_treated_consistently() -> None:
    """Legacy callers that pass no args (or args=None) still get a stable
    signature; the existing "5 record_tool_call(name) → block" tests
    (above) rely on this behaviour.
    """
    gate = TerminationGate(GateConfig(per_tool_max_consecutive=3))
    for _ in range(3):
        gate.record_tool_call("ping")  # no args
    assert gate.state.per_tool_consecutive["ping"] == 3
    ok, reason = gate.allows_tool("ping")
    assert ok is False
    assert reason is TerminationReason.HALLUCINATION_DETECTED


# ---------------------------------------------------------------------------
# 1.4 State progression + error injection
# ---------------------------------------------------------------------------


def test_record_final_answer_terminates_with_success() -> None:
    """1.16 record_final_answer() -> terminated=True, reason=SUCCESS."""
    gate = TerminationGate()
    gate.record_final_answer()
    assert gate.state.terminated is True
    assert gate.state.terminated_reason is TerminationReason.SUCCESS


def test_record_error_propagates_reason() -> None:
    """1.17 record_error(ALL_PROVIDERS_FAILED) -> terminated=True, reason matches."""
    gate = TerminationGate()
    gate.record_error(TerminationReason.ALL_PROVIDERS_FAILED)
    assert gate.state.terminated is True
    assert gate.state.terminated_reason is TerminationReason.ALL_PROVIDERS_FAILED


def test_cost_delta_accumulates() -> None:
    """1.18 record_turn(cost_delta=0.05) × 3 -> state.cost_usd ≈ 0.15."""
    gate = TerminationGate()
    for _ in range(3):
        gate.record_turn(cost_delta_usd=0.05)
    assert gate.state.cost_usd == pytest.approx(0.15)
    assert gate.state.turns_used == 3


# ---------------------------------------------------------------------------
# Additional sanity checks (helpful, optional)
# ---------------------------------------------------------------------------


def test_summary_reports_terminated_reason_after_termination() -> None:
    """summary['reason'] reflects terminated_reason.value once terminated."""
    gate = TerminationGate()
    gate.terminate(TerminationReason.HARD_MAX_TURNS)
    assert gate.summary()["reason"] == TerminationReason.HARD_MAX_TURNS.value


def test_state_tracks_turns_and_tools_independently() -> None:
    """record_turn and record_tool_call advance separate counters."""
    gate = TerminationGate()
    gate.record_turn()
    gate.record_tool_call("foo")
    gate.record_tool_call("foo")
    assert gate.state.turns_used == 1
    assert gate.state.tools_used == 2


def test_terminated_gate_blocks_allows_tool_too() -> None:
    """allows_tool short-circuits with terminated_reason once terminate() is called."""
    gate = TerminationGate()
    gate.terminate(TerminationReason.CIRCUIT_BREAKER_OPEN)
    ok, reason = gate.allows_tool("read_file")
    assert ok is False
    assert reason is TerminationReason.CIRCUIT_BREAKER_OPEN


def test_max_budget_usd_none_never_blocks_on_cost() -> None:
    """With max_budget_usd=None, cost accumulation never trips HARD_MAX_BUDGET_USD."""
    gate = TerminationGate(GateConfig(max_budget_usd=None))
    for _ in range(10):
        gate.record_turn(cost_delta_usd=1_000.0)
    ok, reason = gate.allows_call()
    assert ok is True
    assert reason is None
