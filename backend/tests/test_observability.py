"""Tests for stage_timer + VRAM detection."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from observability.metrics import stage_timer
from observability.vram import detect_vram_gb, recommend_asr_device


# --- stage_timer ---


@pytest.mark.asyncio
async def test_stage_timer_success_emits_stage_complete(capsys):
    """Runs clean, logs stage_complete with elapsed_ms and context.

    structlog's default config writes to stdout via PrintLogger, so we
    assert on captured stdout rather than pytest's caplog.
    """
    async with stage_timer("asr", conn="c1"):
        await asyncio.sleep(0.01)
    captured = capsys.readouterr().out
    assert "stage_complete" in captured
    assert "stage=asr" in captured
    assert "conn=c1" in captured
    assert "elapsed_ms=" in captured


@pytest.mark.asyncio
async def test_stage_timer_raises_but_still_emits(capsys):
    """Exception propagates AND stage_error is logged with error field."""
    with pytest.raises(ValueError):
        async with stage_timer("asr"):
            raise ValueError("boom")
    captured = capsys.readouterr().out
    assert "stage_error" in captured
    assert "boom" in captured
    assert "stage=asr" in captured


@pytest.mark.asyncio
async def test_stage_timer_context_fields_emitted(capsys):
    """Additional kwargs pass through to the log record."""
    async with stage_timer("tool_invoke", tool_name="get_time", session="s1"):
        pass
    captured = capsys.readouterr().out
    assert "tool_name=get_time" in captured
    assert "session=s1" in captured


# --- VRAM detection ---


def test_detect_vram_when_no_torch(monkeypatch):
    """If torch import fails, return 0.0."""
    import builtins
    orig_import = builtins.__import__

    def stub_import(name, *a, **kw):
        if name == "torch":
            raise ImportError("simulated: torch not installed")
        return orig_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", stub_import)
    assert detect_vram_gb() == 0.0


def test_detect_vram_when_cuda_unavailable():
    """If torch is present but cuda.is_available() is False, return 0.0."""
    try:
        import torch  # type: ignore[import-not-found]
    except Exception:
        pytest.skip("torch not installed — stub test covers the no-torch path")

    with patch.object(torch.cuda, "is_available", return_value=False):
        assert detect_vram_gb() == 0.0


# --- recommend_asr_device ---


def test_recommend_cuda_when_ample_vram():
    with patch("observability.vram.detect_vram_gb", return_value=24.0):
        device, compute = recommend_asr_device(min_gb=4.0)
    assert device == "cuda"
    assert compute == "float16"


def test_recommend_cpu_when_no_vram():
    with patch("observability.vram.detect_vram_gb", return_value=0.0):
        device, compute = recommend_asr_device(min_gb=4.0)
    assert device == "cpu"
    assert compute == "int8"


def test_recommend_respects_custom_threshold():
    with patch("observability.vram.detect_vram_gb", return_value=3.0):
        # With 4GB min → falls back
        assert recommend_asr_device(min_gb=4.0)[0] == "cpu"
        # With 2GB min → fits
        assert recommend_asr_device(min_gb=2.0)[0] == "cuda"


def test_recommend_exactly_at_threshold():
    """Boundary: >= min_gb is cuda."""
    with patch("observability.vram.detect_vram_gb", return_value=4.0):
        assert recommend_asr_device(min_gb=4.0)[0] == "cuda"
