"""Observability primitives: stage timing + Prometheus registry.

Two concerns live here:

1. ``stage_timer`` — async context manager that emits a structured log
   record on exit. Zero-dep (just structlog).
2. Prometheus metrics (P2-1-S6) — ``llm_ttft_seconds`` Histogram and
   ``render()`` for the ``/metrics`` endpoint. Centralized so every
   module imports the same Histogram instance (otherwise each module
   creating its own Histogram would double-register on the default
   registry).
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Histogram,
    generate_latest,
)

_default_logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Prometheus registry (P2-1-S6)
# ---------------------------------------------------------------------------

# Buckets chosen for typical LLM TTFT range:
#   local Ollama: 100ms-2s
#   cloud (DashScope/etc): 200ms-5s
# prometheus_client auto-appends +Inf; the trailing float("inf") here is
# redundant-but-harmless and kept for readability of the bucket list.
_TTFT_BUCKETS = (
    0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, float("inf"),
)

# NOTE: cardinality risk if `model` ever becomes user-supplied. Today
# provider_label ∈ {"local","cloud"} and `model` comes from config, so
# the cross-product is O(#providers × #configured_models). If a future
# slice starts taking model names from request headers / user input,
# aggregate to a bounded label set before passing through.
llm_ttft_seconds = Histogram(
    "llm_ttft_seconds",
    "Time from chat_stream call to first yielded token, by provider+model",
    labelnames=["provider", "model"],
    buckets=_TTFT_BUCKETS,
)


def render() -> tuple[bytes, str]:
    """Render current Prometheus metrics in text format.

    Returns ``(body, content_type)`` — the caller (FastAPI route) passes
    both to ``Response``. Content-type includes the protocol version
    expected by scrapers.
    """
    return generate_latest(), CONTENT_TYPE_LATEST


@asynccontextmanager
async def stage_timer(
    name: str,
    logger: structlog.stdlib.BoundLogger | None = None,
    **ctx: object,
) -> AsyncIterator[None]:
    """Time the code inside the `async with` and emit a log record.

    - Success: logs "stage_complete" with stage, elapsed_ms, **ctx
    - Exception: logs "stage_error" with stage, elapsed_ms, error, **ctx,
      then re-raises (exception still propagates to caller).
    """
    log = logger or _default_logger
    start = time.monotonic()
    try:
        yield
    except Exception as exc:
        elapsed_ms = round((time.monotonic() - start) * 1000.0, 1)
        log.warning(
            "stage_error",
            stage=name,
            elapsed_ms=elapsed_ms,
            error=str(exc),
            **ctx,
        )
        raise
    else:
        elapsed_ms = round((time.monotonic() - start) * 1000.0, 1)
        log.info("stage_complete", stage=name, elapsed_ms=elapsed_ms, **ctx)
