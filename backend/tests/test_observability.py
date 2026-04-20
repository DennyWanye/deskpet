"""Tests for stage_timer + VRAM detection."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from observability.metrics import stage_timer
from observability.vram import detect_vram_gb, recommend_asr_device


# --- stage_timer ---


@pytest.mark.asyncio
async def test_stage_timer_success_emits_stage_complete(caplog):
    """Runs clean, logs stage_complete with elapsed_ms and context.

    P2-2-M3 (2026-04-20): main.py now configures structlog to route
    through stdlib logging (so logs/backend.log gets populated). That
    means the event lands in caplog, not stdout — swap capsys→caplog.
    """
    import logging
    with caplog.at_level(logging.INFO):
        async with stage_timer("asr", conn="c1"):
            await asyncio.sleep(0.01)
    text = caplog.text
    assert "stage_complete" in text
    assert "stage='asr'" in text or "stage=asr" in text
    assert "conn='c1'" in text or "conn=c1" in text
    assert "elapsed_ms=" in text


@pytest.mark.asyncio
async def test_stage_timer_raises_but_still_emits(caplog):
    """Exception propagates AND stage_error is logged with error field."""
    import logging
    with caplog.at_level(logging.INFO):
        with pytest.raises(ValueError):
            async with stage_timer("asr"):
                raise ValueError("boom")
    text = caplog.text
    assert "stage_error" in text
    assert "boom" in text
    assert "stage='asr'" in text or "stage=asr" in text


@pytest.mark.asyncio
async def test_stage_timer_context_fields_emitted(caplog):
    """Additional kwargs pass through to the log record."""
    import logging
    with caplog.at_level(logging.INFO):
        async with stage_timer("tool_invoke", tool_name="get_time", session="s1"):
            pass
    text = caplog.text
    assert "tool_name='get_time'" in text or "tool_name=get_time" in text
    assert "session='s1'" in text or "session=s1" in text


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
