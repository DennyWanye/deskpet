"""Tests for observability.metrics Prometheus registry (P2-1-S6)."""
from __future__ import annotations

from observability.metrics import llm_ttft_seconds, render


def test_render_returns_prometheus_format():
    body, content_type = render()
    assert isinstance(body, bytes)
    assert "text/plain" in content_type
    # The metric metadata (HELP/TYPE lines) should appear even with zero observations.
    assert b"llm_ttft_seconds" in body


def test_observe_records_to_histogram():
    # Use a label combo unlikely to be used by other tests so count==1 holds.
    llm_ttft_seconds.labels(provider="test_local", model="test-metric").observe(0.123)
    body, _ = render()
    # Prometheus label ordering can vary; accept either.
    assert (
        b'llm_ttft_seconds_count{model="test-metric",provider="test_local"} 1.0' in body
        or b'llm_ttft_seconds_count{provider="test_local",model="test-metric"} 1.0' in body
    )
