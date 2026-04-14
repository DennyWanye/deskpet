"""Crash-report infrastructure (V5 §7.2).

Installs a ``sys.excepthook`` that writes the uncaught-exception traceback
to ``crash_reports/python-<timestamp>.log`` under the project root. We also
route ``asyncio`` default-exception-handler unhandled task exceptions to
the same place, since many backend crashes live inside asyncio tasks
rather than the main thread.

Design:
- Writes are best-effort (``try/except``): a crash-reporter that itself
  crashes is worse than silent.
- The hook CHAINS to the previous hook so pytest / uvicorn still see the
  traceback on stderr.
- Report files are append-only — the app never reads them back; a future
  config-panel slice can expose them in the UI.
"""
from __future__ import annotations

import asyncio
import sys
import time
import traceback
from pathlib import Path

import structlog

logger = structlog.get_logger()

_DEFAULT_DIR = Path(__file__).resolve().parents[2] / "crash_reports"


def _write_report(kind: str, text: str, directory: Path) -> Path | None:
    """Write a crash dump to ``<dir>/<kind>-<ts>.log``. Returns the path, or
    ``None`` on failure (never raises)."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        path = directory / f"{kind}-{ts}.log"
        path.write_text(text, encoding="utf-8")
        return path
    except Exception:  # pragma: no cover — best-effort
        return None


def install_crash_reporter(directory: Path | None = None) -> None:
    """Hook ``sys.excepthook`` + asyncio loop handler.

    Idempotent: calling twice replaces the prior hook; the previous one is
    chained so nothing is silently dropped.
    """
    directory = directory or _DEFAULT_DIR
    previous_hook = sys.excepthook

    def handle_exception(exc_type, exc_value, tb):  # noqa: ANN001
        if issubclass(exc_type, KeyboardInterrupt):
            # Let Ctrl+C behave normally — not a crash.
            previous_hook(exc_type, exc_value, tb)
            return
        body = "".join(traceback.format_exception(exc_type, exc_value, tb))
        path = _write_report("python", body, directory)
        logger.error("uncaught_exception", path=str(path) if path else None)
        previous_hook(exc_type, exc_value, tb)

    sys.excepthook = handle_exception

    # asyncio task exceptions don't go through sys.excepthook — install a
    # loop-level handler too. Can't set a loop policy globally (FastAPI
    # creates its own loop via uvicorn), so we patch at runtime via a
    # best-effort try on the running loop if one exists.
    try:
        loop = asyncio.get_event_loop()

        def on_loop_exception(_loop, context):
            exc = context.get("exception")
            if exc is None:
                msg = context.get("message", "unknown")
                body = f"asyncio error (no exception): {msg}\ncontext={context!r}\n"
            else:
                body = "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                )
            path = _write_report("asyncio", body, directory)
            logger.error(
                "asyncio_task_exception",
                error=str(exc) if exc else context.get("message"),
                path=str(path) if path else None,
            )

        loop.set_exception_handler(on_loop_exception)
    except RuntimeError:  # no current loop — that's fine, hook just won't fire
        pass

    logger.info("crash_reporter_installed", directory=str(directory))
