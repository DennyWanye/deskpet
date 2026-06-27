# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""WI-12 — tests for observability.metrics_sink.

Coverage:
  * sanitize_detail: scalars kept, long strings dropped, containers dropped
  * record: whitelist enforced, unknown event dropped
  * record: appends one line per call, valid JSON
  * record: detail sanitized on the way in
  * no conversation content / api_key can ever land in the file
  * rotation: file > cap → truncated to tail
  * read_all / summary aggregation
  * I/O failure is swallowed (returns False, no raise)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from observability.metrics_sink import (
    MetricsSink,
    sanitize_detail,
    record as module_record,
    get_default_sink,
    VALID_EVENTS,
    _MAX_DETAIL_STR,
    _MAX_BYTES,
)


# ----------------------------------------------------------------------
# sanitize_detail
# ----------------------------------------------------------------------


def test_sanitize_none_returns_empty() -> None:
    assert sanitize_detail(None) == {}
    assert sanitize_detail({}) == {}


def test_sanitize_keeps_whitelisted_scalars() -> None:
    out = sanitize_detail({"error_class": "timeout", "count": 3, "ratio": 0.5, "ok": True})
    assert out == {"error_class": "timeout", "count": 3, "ratio": 0.5, "ok": True}


def test_sanitize_drops_non_whitelisted_keys() -> None:
    # The primary privacy wall — these key names are not on the list.
    out = sanitize_detail({
        "user_message": "short but secret",
        "prompt": "x",
        "api_key": "sk-xyz",
        "error_class": "kept",
    })
    assert out == {"error_class": "kept"}


def test_sanitize_drops_long_strings_even_on_whitelisted_key() -> None:
    long = "x" * (_MAX_DETAIL_STR + 1)
    out = sanitize_detail({"error_class": "short", "reason": long})
    assert "error_class" in out
    assert "reason" not in out  # whitelisted key, but value too long → dropped


def test_sanitize_keeps_boundary_length_string() -> None:
    exact = "y" * _MAX_DETAIL_STR
    out = sanitize_detail({"error_class": exact})
    assert out["error_class"] == exact


def test_sanitize_drops_containers() -> None:
    out = sanitize_detail({
        "count": {"a": 1},        # whitelisted key but container value
        "ratio": [1, 2, 3],       # whitelisted key but container value
        "skill_name": "kept",
    })
    assert out == {"skill_name": "kept"}


# ----------------------------------------------------------------------
# record — whitelist + append
# ----------------------------------------------------------------------


@pytest.fixture
def sink(tmp_path: Path) -> MetricsSink:
    return MetricsSink(tmp_path / "metrics.jsonl")


def test_record_unknown_event_dropped(sink: MetricsSink) -> None:
    assert sink.record("hacker_event", {"x": 1}) is False
    assert not sink.path.exists()


def test_record_valid_event_appends(sink: MetricsSink) -> None:
    assert sink.record("app_start") is True
    assert sink.record("llm_call_ok", {"latency_ms": 120}) is True
    lines = sink.path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    for ln in lines:
        obj = json.loads(ln)  # each line must be valid JSON
        assert obj["event"] in VALID_EVENTS
        assert "ts" in obj and "detail" in obj


def test_record_all_whitelisted_events(sink: MetricsSink) -> None:
    for ev in VALID_EVENTS:
        assert sink.record(ev) is True
    assert len(sink.read_all()) == len(VALID_EVENTS)


def test_record_sanitizes_detail_on_write(sink: MetricsSink) -> None:
    leaky = "私密对话内容" * 50  # non-whitelisted key — must be dropped
    sink.record("llm_call_failed", {"error_class": "timeout", "transcript": leaky})
    rows = sink.read_all()
    assert len(rows) == 1
    detail = rows[0]["detail"]
    assert detail.get("error_class") == "timeout"
    assert "transcript" not in detail  # key not whitelisted → never persisted


def test_record_ts_override(sink: MetricsSink) -> None:
    sink.record("app_start", now=1700000000.0)
    assert sink.read_all()[0]["ts"] == 1700000000.0


# ----------------------------------------------------------------------
# Privacy invariant — the headline guarantee
# ----------------------------------------------------------------------


def test_no_conversation_content_can_leak(sink: MetricsSink) -> None:
    """Even a caller that *tries* to log secrets must fail — the key
    whitelist makes ``user_message`` / ``api_key`` structurally
    impossible to persist, regardless of value length."""
    conversation = "用户说：我的银行卡密码是 123456"  # SHORT — length cap won't help
    sink.record("llm_call_ok", {
        "user_message": conversation,    # key not whitelisted → dropped
        "api_key": "sk-secret-xyz",      # key not whitelisted → dropped
        "prompt": "我的密码是 abc",       # key not whitelisted → dropped
        "error_class": "none",           # whitelisted → kept
    })
    raw = sink.path.read_text(encoding="utf-8")
    assert "银行卡密码" not in raw
    assert "123456" not in raw
    assert "sk-secret-xyz" not in raw   # key whitelist blocks it — short or not
    assert "我的密码" not in raw
    assert "error_class" in raw         # the one legit field survives


def test_secrets_blocked_by_key_not_length(sink: MetricsSink) -> None:
    """A short secret on a non-whitelisted key must still be dropped —
    proves the wall is the KEY whitelist, not the length cap."""
    for key in ("prompt", "completion", "content", "password", "token"):
        sink.record("llm_call_ok", {key: "shortsecret"})
    raw = sink.path.read_text(encoding="utf-8") if sink.path.exists() else ""
    assert "shortsecret" not in raw


# ----------------------------------------------------------------------
# Rotation
# ----------------------------------------------------------------------


def test_rotation_truncates_to_tail(tmp_path: Path) -> None:
    p = tmp_path / "metrics.jsonl"
    # Pre-fill the file past the cap with junk lines.
    big_line = json.dumps({"ts": 0, "event": "app_start", "detail": {"pad": "x" * 200}})
    n_lines = (_MAX_BYTES // len(big_line)) + 500
    p.write_text("\n".join([big_line] * n_lines) + "\n", encoding="utf-8")
    assert p.stat().st_size > _MAX_BYTES

    sink = MetricsSink(p)
    sink.record("crash", {"file": "x.txt"})  # triggers rotation

    assert p.stat().st_size <= _MAX_BYTES
    rows = sink.read_all()
    # Tail kept + our new row is the last
    assert rows[-1]["event"] == "crash"


def test_no_rotation_below_cap(sink: MetricsSink) -> None:
    for _ in range(50):
        sink.record("app_start")
    assert len(sink.read_all()) == 50  # nothing dropped


# ----------------------------------------------------------------------
# read_all / summary
# ----------------------------------------------------------------------


def test_read_all_skips_bad_lines(tmp_path: Path) -> None:
    p = tmp_path / "metrics.jsonl"
    p.write_text(
        '{"ts":1,"event":"app_start","detail":{}}\n'
        "not json at all\n"
        '{"ts":2,"event":"crash","detail":{}}\n',
        encoding="utf-8",
    )
    rows = MetricsSink(p).read_all()
    assert len(rows) == 2


def test_read_all_missing_file(sink: MetricsSink) -> None:
    assert sink.read_all() == []


def test_summary_aggregates(sink: MetricsSink) -> None:
    sink.record("app_start")
    sink.record("app_start")
    sink.record("llm_call_ok")
    sink.record("llm_call_failed")
    sink.record("llm_call_failed")
    sink.record("llm_call_failed")
    s = sink.summary()
    assert s == {"app_start": 2, "llm_call_ok": 1, "llm_call_failed": 3}


def test_summary_empty(sink: MetricsSink) -> None:
    assert sink.summary() == {}


# ----------------------------------------------------------------------
# Failure isolation
# ----------------------------------------------------------------------


def test_record_io_failure_swallowed(tmp_path: Path, monkeypatch) -> None:
    sink = MetricsSink(tmp_path / "metrics.jsonl")

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "open", _boom)
    # Must return False, never raise.
    assert sink.record("app_start") is False


# ----------------------------------------------------------------------
# Default sink
# ----------------------------------------------------------------------


def test_get_default_sink_is_singleton() -> None:
    a = get_default_sink()
    b = get_default_sink()
    assert a is b


def test_module_record_uses_default_sink() -> None:
    # Should not raise; return type is bool.
    result = module_record("app_start")
    assert isinstance(result, bool)
