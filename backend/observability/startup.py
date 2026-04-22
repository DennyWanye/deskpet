"""P3-S2 — structured startup-error registry.

Lifespan (`backend/main.py`) loads ASR/TTS/VAD engines best-effort.
Before this module, failures were swallowed into a `logger.warning`
and lost — the frontend saw a "ready" backend whose ASR endpoint
500'd on first request.

`StartupErrorRegistry` keeps the classified failures in memory so
that `/health` can return `status: "degraded"` with a machine-readable
list, and the WS control channel can push `startup_status` as its
first frame after handshake.

Design choices:
- Pure `_classify(exc)` so the mapping is unit-testable without
  spinning up FastAPI.
- Registry de-dupes by engine name (lifespan retries overwrite).
- `snapshot()` returns plain dicts — JSON-safe for both the HTTP
  and WS paths. No datetime, no Exception instances.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Tuple


# Error-code constants — callers (tests, /health consumers) depend on
# these being stable strings. Don't rename without bumping the API.
CUDA_UNAVAILABLE = "CUDA_UNAVAILABLE"
MODEL_DIR_MISSING = "MODEL_DIR_MISSING"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class StartupError:
    engine: str
    error_code: str
    error_message: str  # user-facing, Chinese OK
    raw: str            # repr(exc) — for log correlation, not UI


def _classify(exc: BaseException) -> Tuple[str, str]:
    """Map an exception from `engine.load()` to (error_code, user_message).

    Rules (checked in order — most specific first):
    1. "no such file or directory" anywhere in the message → MODEL_DIR_MISSING
       (CTranslate2 on Windows raises bare RuntimeError with this string
        even when the real cause is a missing model.bin — treat it as
        missing-dir, not CUDA).
    2. FileNotFoundError → MODEL_DIR_MISSING.
    3. "cuda" (case-insensitive) in the message → CUDA_UNAVAILABLE.
    4. Otherwise UNKNOWN.
    """
    msg = str(exc)
    lower = msg.lower()

    if "no such file or directory" in lower:
        return (
            MODEL_DIR_MISSING,
            "模型文件缺失，请确认 backend/models/ 目录下已解压对应模型。",
        )
    if isinstance(exc, FileNotFoundError):
        return (
            MODEL_DIR_MISSING,
            "模型文件缺失，请确认 backend/models/ 目录下已解压对应模型。",
        )
    if "cuda" in lower:
        return (
            CUDA_UNAVAILABLE,
            "无法初始化 CUDA，请确认已安装最新版 NVIDIA 驱动并重启。",
        )
    return (UNKNOWN, f"启动阶段出错：{msg}")


class StartupErrorRegistry:
    """In-memory store of per-engine load failures.

    Not thread-safe by construction — FastAPI lifespan runs sequentially
    on a single event-loop thread, which is the only writer. Readers
    (/health handler, WS on_connect) are also on the same loop.
    """

    def __init__(self) -> None:
        # engine name → StartupError. Dict preserves insertion order for
        # stable snapshot ordering; overwrite-by-key gives the retry
        # semantics the tests assert.
        self._errors: dict[str, StartupError] = {}

    def record(self, engine: str, exc: BaseException) -> None:
        code, message = _classify(exc)
        self._errors[engine] = StartupError(
            engine=engine,
            error_code=code,
            error_message=message,
            raw=repr(exc),
        )

    def snapshot(self) -> list[dict]:
        return [asdict(e) for e in self._errors.values()]

    def is_degraded(self) -> bool:
        return bool(self._errors)

    def clear(self) -> None:
        self._errors.clear()


# Module-level singleton — imported by `main.py` lifespan + /health.
# Tests instantiate their own `StartupErrorRegistry()` to stay isolated.
registry = StartupErrorRegistry()
