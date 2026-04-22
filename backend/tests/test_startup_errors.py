"""P3-S2 — startup error registry & classification tests.

Red phase: these tests describe the contract the upcoming
`backend/observability/startup.py` module must satisfy:

1. Pure `_classify(exc)` maps exceptions to stable error_code strings.
2. `StartupErrorRegistry.record()` de-duplicates by engine name
   (later record wins — matches lifespan retry semantics).
3. `snapshot()` returns JSON-serialisable dicts suitable for
   /health and WS startup_status payloads.
"""
from __future__ import annotations

import pytest

from observability.startup import (  # type: ignore[import-not-found]
    StartupError,
    StartupErrorRegistry,
    _classify,
)


# ---------------------------------------------------------------------------
# _classify
# ---------------------------------------------------------------------------

def test_classify_cuda_runtime_error_detected():
    exc = RuntimeError("CUDA driver is not available")
    code, message = _classify(exc)
    assert code == "CUDA_UNAVAILABLE"
    # user-facing message must mention NVIDIA driver in some form
    assert "NVIDIA" in message or "驱动" in message


def test_classify_cuda_case_insensitive():
    # ctranslate2 sometimes says "cuda" lowercase in the message
    code, _ = _classify(RuntimeError("failed to initialize cuda context"))
    assert code == "CUDA_UNAVAILABLE"


def test_classify_missing_model_dir_via_filenotfound():
    exc = FileNotFoundError("models/faster-whisper-large-v3-turbo/model.bin")
    code, message = _classify(exc)
    assert code == "MODEL_DIR_MISSING"
    assert "model" in message.lower() or "模型" in message


def test_classify_missing_model_dir_via_oserror_message():
    # Windows CTranslate2 sometimes raises RuntimeError with a
    # "No such file or directory" substring rather than FileNotFoundError.
    exc = RuntimeError("Unable to open file: No such file or directory")
    code, _ = _classify(exc)
    assert code == "MODEL_DIR_MISSING"


def test_classify_unknown_falls_through():
    code, _ = _classify(ValueError("random bug"))
    assert code == "UNKNOWN"


# ---------------------------------------------------------------------------
# StartupErrorRegistry
# ---------------------------------------------------------------------------

def test_registry_starts_empty():
    reg = StartupErrorRegistry()
    assert reg.snapshot() == []
    assert reg.is_degraded() is False


def test_registry_record_then_snapshot():
    reg = StartupErrorRegistry()
    reg.record("asr_engine", RuntimeError("CUDA driver is not available"))

    snap = reg.snapshot()
    assert len(snap) == 1
    entry = snap[0]
    assert entry["engine"] == "asr_engine"
    assert entry["error_code"] == "CUDA_UNAVAILABLE"
    assert isinstance(entry["error_message"], str)
    assert entry["error_message"]  # non-empty
    # `raw` carries repr(exc) for log correlation but must also be string
    assert "CUDA" in entry["raw"]


def test_registry_degraded_when_nonempty():
    reg = StartupErrorRegistry()
    reg.record("asr_engine", RuntimeError("cuda fail"))
    assert reg.is_degraded() is True


def test_registry_same_engine_overwrites():
    """If lifespan retries and gets a different failure, last one wins."""
    reg = StartupErrorRegistry()
    reg.record("asr_engine", RuntimeError("cuda"))
    reg.record("asr_engine", FileNotFoundError("models/..."))
    snap = reg.snapshot()
    assert len(snap) == 1
    assert snap[0]["error_code"] == "MODEL_DIR_MISSING"


def test_registry_multiple_engines_coexist():
    reg = StartupErrorRegistry()
    reg.record("asr_engine", RuntimeError("cuda"))
    reg.record("tts_engine", FileNotFoundError("cosyvoice2/"))
    snap = reg.snapshot()
    engines = sorted(e["engine"] for e in snap)
    assert engines == ["asr_engine", "tts_engine"]


def test_registry_clear_resets_state():
    reg = StartupErrorRegistry()
    reg.record("asr_engine", RuntimeError("cuda"))
    reg.clear()
    assert reg.snapshot() == []
    assert reg.is_degraded() is False


def test_snapshot_is_json_serialisable():
    import json
    reg = StartupErrorRegistry()
    reg.record("asr_engine", RuntimeError("cuda driver not found"))
    # raises TypeError if not serialisable
    encoded = json.dumps(reg.snapshot())
    assert "CUDA_UNAVAILABLE" in encoded


# ---------------------------------------------------------------------------
# StartupError dataclass shape
# ---------------------------------------------------------------------------

def test_startup_error_fields():
    err = StartupError(
        engine="asr_engine",
        error_code="CUDA_UNAVAILABLE",
        error_message="CUDA driver missing",
        raw="RuntimeError('cuda')",
    )
    assert err.engine == "asr_engine"
    assert err.error_code == "CUDA_UNAVAILABLE"
