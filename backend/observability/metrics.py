"""Lightweight stage timing — async context manager that emits a
structured log record on exit.

Keeps dependency footprint zero (just structlog, already in deps).
If we later want Prometheus/OTLP export, wrap these logs with a hook —
the structured fields (stage, elapsed_ms) are already standardized.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog

_default_logger = structlog.get_logger()


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
