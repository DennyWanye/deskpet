"""Tests for S8: 4-tier VRAM classifier + clipboard/reminder tools."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from observability.vram import classify_tier
from tools.clipboard import ReadClipboardTool
from tools.reminder import (
    ListRemindersTool,
    _reset_for_testing,
    add_reminder,
)


# --- classify_tier ---


@pytest.mark.parametrize(
    "vram, expected",
    [
        (48.0, "flagship"),
        (36.0, "flagship"),
        (34.9, "standard"),
        (25.0, "standard"),
        (24.9, "economy"),
        (15.0, "economy"),
        (14.9, "minimal"),
        (0.0, "minimal"),
    ],
)
def test_classify_tier_boundaries(vram: float, expected: str):
    assert classify_tier(vram).tier == expected


def test_classify_tier_probes_live_when_no_arg():
    """Without vram arg, classify_tier must call detect_vram_gb."""
    with patch("observability.vram.detect_vram_gb", return_value=0.0):
        tier = classify_tier()
    assert tier.tier == "minimal"
    assert tier.asr_device == "cpu"


def test_flagship_tier_is_full_local_stack():
    tier = classify_tier(48.0)
    assert tier.llm_model == "gemma:27b"
    assert tier.tts_model == "cosyvoice2"
    assert tier.asr_device == "cuda"
    assert tier.asr_compute == "float16"


def test_minimal_tier_falls_back_to_cloud_tts():
    tier = classify_tier(0.0)
    assert tier.tts_model == "edge-tts"
    assert tier.asr_device == "cpu"


# --- reminder tool ---


@pytest.mark.asyncio
async def test_reminder_tool_empty_returns_empty_string():
    _reset_for_testing()
    tool = ListRemindersTool()
    assert await tool.invoke() == ""


@pytest.mark.asyncio
async def test_reminder_tool_formats_hhmm_prefix():
    _reset_for_testing()
    add_reminder("pick up groceries")
    add_reminder("call mom")

    tool = ListRemindersTool()
    out = await tool.invoke()
    lines = out.split("\n")

    assert len(lines) == 2
    # HH:MM prefix means 5 chars + ":" + space + text
    assert lines[0][2] == ":" and lines[0][5] == ":"
    assert "pick up groceries" in out
    assert "call mom" in out


@pytest.mark.asyncio
async def test_reminder_spec_low_risk():
    assert ListRemindersTool.spec.requires_confirmation is False


# --- clipboard tool (mocked) ---


@pytest.mark.asyncio
async def test_clipboard_tool_returns_payload_from_platform_impl(monkeypatch):
    """We don't actually touch the OS clipboard — patch the helper."""
    tool = ReadClipboardTool()
    monkeypatch.setattr(
        "tools.clipboard._read_clipboard_windows", lambda: "hello from clip"
    )
    monkeypatch.setattr(
        "tools.clipboard._read_clipboard_tk", lambda: "hello from clip"
    )
    out = await tool.invoke()
    assert out == "hello from clip"


@pytest.mark.asyncio
async def test_clipboard_tool_swallows_exceptions(monkeypatch):
    """Errors must return a string, not raise — keeps LLM stream intact."""
    tool = ReadClipboardTool()

    def boom():
        raise RuntimeError("no clipboard for you")

    monkeypatch.setattr("tools.clipboard._read_clipboard_windows", boom)
    monkeypatch.setattr("tools.clipboard._read_clipboard_tk", boom)

    out = await tool.invoke()
    assert "clipboard read failed" in out
    assert "no clipboard for you" in out


@pytest.mark.asyncio
async def test_clipboard_spec_low_risk():
    assert ReadClipboardTool.spec.requires_confirmation is False
