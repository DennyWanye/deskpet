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
    llm_ttft_seconds.labels(provider="local", model="gemma4:e4b").observe(0.123)
    body, _ = render()
    # Prometheus label ordering can vary; accept either.
    assert (
        b'llm_ttft_seconds_count{model="gemma4:e4b",provider="local"} 1.0' in body
        or b'llm_ttft_seconds_count{provider="local",model="gemma4:e4b"} 1.0' in body
    )
