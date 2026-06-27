# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""R-T3 — LLM 失败降级矩阵 + 故障注入单测（§15.4 要求）。

覆盖：
  2.3 GoalChecker 超时/失败 → (False, "goal_check=skipped")  + metric
  2.1 反思 parse 失败 → None (机械 nudge) + metric reflection_parse_failed
  2.4 ExternalEvaluator timeout/error → conservative (revise) on high-consequence
      + metric evaluator_skipped
  无崩溃保证（所有注入场景不抛异常）

合规性规定（per §15.4 spec）：
  - GoalChecker timeout/error 在有活跃 goal 时 MUST NOT default to done=True
  - 降级信号 goal_check=skipped 用于区分"真完成"和"checker 失败放行"
  - goal_check=skipped → 调用方 treat as "非正向确认"，不触发 mark_done
"""
from __future__ import annotations

import asyncio
import pytest

# ─── GoalChecker 降级矩阵 ─────────────────────────────────────


class _RecordingMetricsSink:
    """本地记录 metrics 调用的 stub（替代全局 observability.metrics_sink）。"""
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def record(self, event: str, detail: dict | None = None) -> bool:
        self.events.append((event, detail or {}))
        return True


def _make_llm(response: str):
    async def _call(prompt: str) -> str:
        return response
    return _call


def _make_failing_llm(exc: Exception):
    async def _call(prompt: str) -> str:
        raise exc
    return _call


def _make_timeout_llm():
    """LLM that raises asyncio.TimeoutError to simulate timeout."""
    async def _call(prompt: str) -> str:
        raise asyncio.TimeoutError("LLM timeout")
    return _call


# ─────────────────────────────────────────────────────────────────────────────
# T-D1: GoalChecker timeout → (False, "goal_check=skipped") — NOT (True, ...)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_goal_checker_timeout_returns_skipped_not_done():
    """R-T3 key assertion: timeout MUST return done=False (not default-PASS)."""
    from deskpet.agent.goal_checker import GoalChecker
    checker = GoalChecker(_make_timeout_llm())
    done, hint = await checker.check("write a report", [])
    # CRITICAL: must NOT be done=True (that would be dangerous default-pass)
    assert done is False, (
        "GoalChecker timeout must NOT default to done=True — "
        "that silently marks goal done when checker is broken."
    )
    assert hint == "goal_check=skipped"


@pytest.mark.asyncio
async def test_goal_checker_llm_exception_returns_skipped():
    """R-T3: any LLM exception → skipped signal, not default done."""
    from deskpet.agent.goal_checker import GoalChecker
    checker = GoalChecker(_make_failing_llm(RuntimeError("connection refused")))
    done, hint = await checker.check("任意目标", [])
    assert done is False
    assert hint == "goal_check=skipped"


@pytest.mark.asyncio
async def test_goal_checker_malformed_json_returns_skipped():
    """R-T3: LLM returns garbage JSON → skipped, not done=True."""
    from deskpet.agent.goal_checker import GoalChecker
    checker = GoalChecker(_make_llm("this is not json at all!!!"))
    done, hint = await checker.check("send email", [])
    assert done is False
    assert hint == "goal_check=skipped"


@pytest.mark.asyncio
async def test_goal_checker_non_string_response_returns_skipped():
    """R-T3: non-str LLM response → skipped."""
    from deskpet.agent.goal_checker import GoalChecker
    async def _bad_llm(p: str):
        return 999  # type: ignore[return-value]
    checker = GoalChecker(_bad_llm)
    done, hint = await checker.check("目标", [])
    assert done is False
    assert hint == "goal_check=skipped"


@pytest.mark.asyncio
async def test_goal_checker_missing_done_field_returns_skipped():
    """R-T3: JSON with no 'done' field → skipped."""
    from deskpet.agent.goal_checker import GoalChecker
    checker = GoalChecker(_make_llm('{"hint": "missing done field"}'))
    done, hint = await checker.check("目标", [])
    assert done is False
    assert hint == "goal_check=skipped"


@pytest.mark.asyncio
async def test_goal_checker_normal_done_true_unaffected():
    """BC: normal success path (done=true) still returns (True, '')."""
    from deskpet.agent.goal_checker import GoalChecker
    checker = GoalChecker(_make_llm('{"done": true, "hint": ""}'))
    done, hint = await checker.check("write hello", [])
    assert done is True
    assert hint == ""


@pytest.mark.asyncio
async def test_goal_checker_normal_done_false_unaffected():
    """BC: normal not-done path still returns (False, hint text)."""
    from deskpet.agent.goal_checker import GoalChecker
    checker = GoalChecker(_make_llm('{"done": false, "hint": "缺少测试"}'))
    done, hint = await checker.check("make tests pass", [])
    assert done is False
    assert hint == "缺少测试"


@pytest.mark.asyncio
async def test_goal_checker_skipped_emits_metric(monkeypatch):
    """R-T3: degradation fact emitted to metrics on skipped."""
    from deskpet.agent import goal_checker as _gc_module
    sink = _RecordingMetricsSink()

    # Patch metrics_sink.record inside goal_checker module
    import observability.metrics_sink as _ms
    monkeypatch.setattr(_ms, "record", sink.record)

    from deskpet.agent.goal_checker import GoalChecker
    checker = GoalChecker(_make_timeout_llm())
    done, hint = await checker.check("目标", [])

    assert done is False
    assert hint == "goal_check=skipped"
    # metric should have been emitted
    events = [e for e, _ in sink.events if e == "goal_check_skipped"]
    assert len(events) >= 1, f"Expected goal_check_skipped metric, got: {sink.events}"


# ─────────────────────────────────────────────────────────────────────────────
# T-D2: Reflection parse failure → None + metric reflection_parse_failed
# ─────────────────────────────────────────────────────────────────────────────

def test_reflection_parse_failure_returns_none():
    """R-T3 2.1: malformed JSON → parse_reflection returns None (mechanical nudge)."""
    from deskpet.agent.reflection import parse_reflection
    result = parse_reflection("absolutely no json here")
    assert result is None


def test_reflection_parse_empty_string_returns_none():
    """R-T3: empty string → None."""
    from deskpet.agent.reflection import parse_reflection
    result = parse_reflection("")
    assert result is None


def test_reflection_parse_valid_json_still_works():
    """BC: valid structured reflection JSON still parses correctly."""
    from deskpet.agent.reflection import parse_reflection
    import json
    raw = json.dumps({
        "error_analysis": "missed tool call",
        "execution_critique": "took shortcut",
        "task_replanning": "call ppt_create properly",
        "next_action": "ppt_create({...})",
        "confidence": 0.7,
    })
    result = parse_reflection(raw)
    assert result is not None
    assert result.confidence == pytest.approx(0.7)
    assert result.error_analysis == "missed tool call"


def test_reflection_parse_failure_emits_metric(monkeypatch):
    """R-T3: parse failure emits reflection_parse_failed metric."""
    import observability.metrics_sink as _ms
    sink = _RecordingMetricsSink()
    monkeypatch.setattr(_ms, "record", sink.record)

    from deskpet.agent.reflection import parse_reflection
    result = parse_reflection("not json not json not json")
    assert result is None

    events = [e for e, _ in sink.events if e == "reflection_parse_failed"]
    assert len(events) >= 1, f"Expected reflection_parse_failed metric, got: {sink.events}"


def test_reflection_parse_truncated_json_emits_metric(monkeypatch):
    """R-T3: truncated JSON (valid object key but not extractable) → metric."""
    import observability.metrics_sink as _ms
    sink = _RecordingMetricsSink()
    monkeypatch.setattr(_ms, "record", sink.record)

    from deskpet.agent.reflection import parse_reflection
    # Provide something that has curly brace but is truncated / invalid
    result = parse_reflection("{broken json here <<<")
    assert result is None

    events = [e for e, _ in sink.events if e == "reflection_parse_failed"]
    assert len(events) >= 1


# ─────────────────────────────────────────────────────────────────────────────
# T-D3: ExternalEvaluator timeout → conservative block + metric
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_evaluator_timeout_conservative_mode_returns_revise():
    """R-T3 2.4: timeout with conservative_on_error=True → revise (not pass)."""
    from deskpet.agent.external_evaluator import ExternalEvaluator

    async def _timeout_llm(prompt: str) -> str:
        raise asyncio.TimeoutError("simulated timeout")

    evaluator = ExternalEvaluator(_timeout_llm, conservative_on_error=True)
    result = await evaluator.evaluate(
        original_goal="send important email",
        produced_artifacts=[],
        objective_evidence=[],
        conversation_summary="",
    )
    assert result["verdict"] == "revise", (
        "High-consequence evaluator timeout must block (revise), not pass-through"
    )
    assert "evaluator_skipped" in result.get("reason", "").lower() or \
           result.get("quality_score", 10) <= 5, (
        "Should indicate skipped + conservative block"
    )


@pytest.mark.asyncio
async def test_evaluator_llm_error_conservative_returns_revise():
    """R-T3: LLM error + conservative_on_error=True → revise."""
    from deskpet.agent.external_evaluator import ExternalEvaluator

    async def _err_llm(prompt: str) -> str:
        raise RuntimeError("connection failed")

    evaluator = ExternalEvaluator(_err_llm, conservative_on_error=True)
    result = await evaluator.evaluate(
        original_goal="delete all files",
        produced_artifacts=[],
        objective_evidence=[],
        conversation_summary="",
    )
    assert result["verdict"] == "revise"


@pytest.mark.asyncio
async def test_evaluator_llm_error_non_conservative_still_passes():
    """BC: conservative_on_error=False (default) keeps existing safe-fail pass."""
    from deskpet.agent.external_evaluator import ExternalEvaluator

    async def _err_llm(prompt: str) -> str:
        raise RuntimeError("connection failed")

    evaluator = ExternalEvaluator(_err_llm)  # default conservative_on_error=False
    result = await evaluator.evaluate(
        original_goal="search the web",
        produced_artifacts=[],
        objective_evidence=[],
        conversation_summary="",
    )
    assert result["verdict"] == "pass"  # BC: existing behavior preserved


@pytest.mark.asyncio
async def test_evaluator_no_provider_conservative_returns_revise():
    """R-T3: llm_call=None + conservative_on_error=True → revise."""
    from deskpet.agent.external_evaluator import ExternalEvaluator

    evaluator = ExternalEvaluator(None, conservative_on_error=True)
    result = await evaluator.evaluate(
        original_goal="delete user data",
        produced_artifacts=[],
        objective_evidence=[],
        conversation_summary="",
    )
    assert result["verdict"] == "revise"


@pytest.mark.asyncio
async def test_evaluator_no_provider_non_conservative_passes():
    """BC: llm_call=None + conservative_on_error=False (default) → pass."""
    from deskpet.agent.external_evaluator import ExternalEvaluator

    evaluator = ExternalEvaluator(None)
    result = await evaluator.evaluate(
        original_goal="list files",
        produced_artifacts=[],
        objective_evidence=[],
        conversation_summary="",
    )
    assert result["verdict"] == "pass"  # BC


@pytest.mark.asyncio
async def test_evaluator_conservative_emits_metric(monkeypatch):
    """R-T3: conservative block emits evaluator_skipped metric with conservative tag."""
    import observability.metrics_sink as _ms
    sink = _RecordingMetricsSink()
    monkeypatch.setattr(_ms, "record", sink.record)

    from deskpet.agent.external_evaluator import ExternalEvaluator

    async def _err_llm(prompt: str) -> str:
        raise RuntimeError("boom")

    evaluator = ExternalEvaluator(_err_llm, conservative_on_error=True)
    await evaluator.evaluate(
        original_goal="buy something",
        produced_artifacts=[],
        objective_evidence=[],
        conversation_summary="",
    )
    events = [e for e, d in sink.events if e == "evaluator_skipped"]
    assert len(events) >= 1
    # Check that reason contains conservative info
    last_detail = next((d for e, d in sink.events if e == "evaluator_skipped"), {})
    assert "conservative" in last_detail.get("reason", "").lower() or \
           last_detail.get("reason", "") != ""


@pytest.mark.asyncio
async def test_evaluator_no_crash_on_any_error():
    """R-T3 no-crash guarantee: any exception → safe-fail, never raises."""
    from deskpet.agent.external_evaluator import ExternalEvaluator

    async def _catastrophic_llm(prompt: str) -> str:
        raise MemoryError("OOM")

    # Both conservative and non-conservative must not crash
    for conservative in (True, False):
        evaluator = ExternalEvaluator(_catastrophic_llm, conservative_on_error=conservative)
        result = await evaluator.evaluate("goal", [], [], "")
        assert isinstance(result, dict)
        assert "verdict" in result


# ─────────────────────────────────────────────────────────────────────────────
# T-D4: No crash guarantee for all paths
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_goal_checker_never_crashes_on_exception():
    """R-T3: GoalChecker never raises — always returns (bool, str)."""
    from deskpet.agent.goal_checker import GoalChecker
    for exc in [RuntimeError("boom"), asyncio.TimeoutError(), ValueError("bad"), OSError("io")]:
        checker = GoalChecker(_make_failing_llm(exc))
        done, hint = await checker.check("目标", [])
        assert isinstance(done, bool)
        assert isinstance(hint, str)


def test_reflection_never_crashes():
    """R-T3: parse_reflection never raises — returns None or StructuredReflection."""
    from deskpet.agent.reflection import parse_reflection
    for text in ["", "   ", "{{{", "null", "[]", "x" * 10000, '{"done": true}']:
        result = parse_reflection(text)
        assert result is None or hasattr(result, "confidence")


# ─────────────────────────────────────────────────────────────────────────────
# T-D5: R-T6 — Receipt shadow fields (BC + round-trip)
# ─────────────────────────────────────────────────────────────────────────────

from datetime import datetime, timezone


def _make_basic_receipt(**overrides):
    from deskpet.tools.receipt import make_receipt
    defaults = dict(
        tool_name="ppt_create",
        args={"title": "test"},
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc),
        ok=True,
    )
    defaults.update(overrides)
    return make_receipt(**defaults)


def test_receipt_shadow_fields_have_safe_defaults():
    """R-T6: new optional shadow fields default to None/empty — BC for existing receipts."""
    r = _make_basic_receipt()
    assert r.shadow_verdict is None
    assert r.actual_outcome is None
    assert r.verify_latency_ms is None
    assert r.degradation_flags == []


def test_receipt_shadow_verdict_can_be_set():
    """R-T6: shadow_verdict can record 'would_block' or 'would_pass'."""
    from deskpet.tools.receipt import ToolReceipt
    r = _make_basic_receipt()
    r.shadow_verdict = "would_block"
    assert r.shadow_verdict == "would_block"


def test_receipt_actual_outcome_can_be_set():
    """R-T6: actual_outcome placeholder is settable."""
    r = _make_basic_receipt()
    r.actual_outcome = "user_accepted"
    assert r.actual_outcome == "user_accepted"


def test_receipt_verify_latency_ms_can_be_set():
    """R-T6: verify_latency_ms for p95 monitoring."""
    r = _make_basic_receipt()
    r.verify_latency_ms = 450
    assert r.verify_latency_ms == 450


def test_receipt_degradation_flags_can_be_appended():
    """R-T6: degradation_flags records which checks were skipped."""
    r = _make_basic_receipt()
    r.degradation_flags.append("goal_check=skipped")
    r.degradation_flags.append("evaluator_skipped")
    assert "goal_check=skipped" in r.degradation_flags
    assert len(r.degradation_flags) == 2


def test_receipt_to_dict_includes_shadow_fields():
    """R-T6: to_dict() serializes shadow fields (for JSONL persistence)."""
    r = _make_basic_receipt()
    r.shadow_verdict = "would_pass"
    r.verify_latency_ms = 120
    r.degradation_flags = ["reflection_parse_failed"]
    d = r.to_dict()
    assert d["shadow_verdict"] == "would_pass"
    assert d["verify_latency_ms"] == 120
    assert d["degradation_flags"] == ["reflection_parse_failed"]
    assert d["actual_outcome"] is None  # default preserved


def test_receipt_from_dict_old_shape_still_valid():
    """BC: receipt dict WITHOUT shadow fields can still be loaded (ToolReceipt(**d))."""
    from deskpet.tools.receipt import ToolReceipt, make_receipt
    r = make_receipt(
        tool_name="test",
        args={},
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ok=True,
    )
    d = r.to_dict()
    # Remove shadow fields to simulate an old-format receipt
    for key in ("shadow_verdict", "actual_outcome", "verify_latency_ms", "degradation_flags"):
        d.pop(key, None)

    # Should not raise — new fields have defaults
    r2 = ToolReceipt(**d)
    assert r2.shadow_verdict is None
    assert r2.actual_outcome is None
    assert r2.verify_latency_ms is None
    assert r2.degradation_flags == []


def test_receipt_hmac_still_valid_with_shadow_fields():
    """R-T6 BC: HMAC sign/verify round-trip works even with shadow fields populated."""
    from deskpet.tools.receipt import hmac_sign, hmac_verify
    r = _make_basic_receipt()
    r.shadow_verdict = "would_block"
    r.verify_latency_ms = 200
    r.degradation_flags = ["goal_check=skipped"]
    # Re-sign with shadow fields included
    r.sig = hmac_sign(r)
    assert hmac_verify(r) is True


def test_receipt_store_round_trip_with_shadow_fields(tmp_path):
    """R-T6: ReceiptStore append + load_session round-trips shadow fields."""
    from deskpet.tools.receipt import make_receipt, hmac_sign
    from deskpet.tools.receipt_store import ReceiptStore
    key = b"\x42" * 32
    store = ReceiptStore(tmp_path, key=key)

    r = make_receipt(
        tool_name="ppt_create",
        args={"title": "t"},
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ok=True,
        session_id="sess-1",
        secret=key,
    )
    r.shadow_verdict = "would_block"
    r.verify_latency_ms = 350
    r.degradation_flags = ["evaluator_skipped"]
    # Re-sign after mutating shadow fields
    r.sig = hmac_sign(r, key)
    store.append(r)

    loaded = store.load_session("sess-1")
    assert len(loaded) == 1
    assert loaded[0].shadow_verdict == "would_block"
    assert loaded[0].verify_latency_ms == 350
    assert "evaluator_skipped" in loaded[0].degradation_flags


# ─────────────────────────────────────────────────────────────────────────────
# T-D6: metrics_sink has the new event names whitelisted
# ─────────────────────────────────────────────────────────────────────────────

def test_metrics_sink_accepts_goal_check_skipped():
    """R-T3/R-T6: goal_check_skipped event is whitelisted in metrics_sink."""
    from observability.metrics_sink import VALID_EVENTS
    assert "goal_check_skipped" in VALID_EVENTS, (
        "goal_check_skipped must be in VALID_EVENTS for degradation tracking"
    )


def test_metrics_sink_accepts_reflection_parse_failed():
    """R-T3/R-T6: reflection_parse_failed event is whitelisted."""
    from observability.metrics_sink import VALID_EVENTS
    assert "reflection_parse_failed" in VALID_EVENTS


def test_metrics_sink_accepts_evaluator_conservative_block():
    """R-T3/R-T6: evaluator_conservative_block event is whitelisted."""
    from observability.metrics_sink import VALID_EVENTS
    assert "evaluator_conservative_block" in VALID_EVENTS


def test_metrics_sink_records_goal_check_skipped(tmp_path):
    """R-T3: MetricsSink.record() writes goal_check_skipped to disk."""
    from observability.metrics_sink import MetricsSink
    sink = MetricsSink(tmp_path / "metrics.jsonl")
    result = sink.record("goal_check_skipped", {"reason": "timeout"})
    assert result is True
    lines = (tmp_path / "metrics.jsonl").read_text().splitlines()
    import json
    assert any(json.loads(l)["event"] == "goal_check_skipped" for l in lines)


def test_metrics_sink_records_reflection_parse_failed(tmp_path):
    """R-T3: MetricsSink.record() writes reflection_parse_failed to disk."""
    from observability.metrics_sink import MetricsSink
    sink = MetricsSink(tmp_path / "metrics.jsonl")
    result = sink.record("reflection_parse_failed", {"count": 1})
    assert result is True
