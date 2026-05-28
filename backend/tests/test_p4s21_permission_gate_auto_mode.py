# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S21 #13 — PermissionGate auto_mode short-circuit + voice TTS hint.

When ``auto_mode == True`` the gate returns ALLOW for every request
without consulting cache, deny patterns, or the responder. When
``current_source == "voice"`` and the popup IS about to fire (auto
mode off), the gate also fires a TTS notification asking the user to
look at the screen — best-effort, never blocks the popup flow.
"""
from __future__ import annotations

import asyncio
import pytest

from deskpet.permissions.gate import PermissionGate, PermissionGateConfig
from deskpet.types.skill_platform import PermissionResponse


# ---------------------------------------------------------------------------
# auto_mode = True → ALLOW everywhere
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_mode_allows_shell_without_responder():
    gate = PermissionGate()
    gate.auto_mode = True
    # Note: no responder set, no cache pre-populated, no deny pattern
    # match. If auto_mode didn't short-circuit we'd get a timeout-deny.
    decision = await gate.check(
        "shell", {"command": "rm -rf /"}, session_id="test"
    )
    assert decision.allow is True
    assert decision.source == "auto-mode"


@pytest.mark.asyncio
async def test_auto_mode_beats_deny_patterns():
    """auto_mode is intentionally above the deny-pattern layer:
    user explicitly opted into 'yes to everything'. If they want denylists
    enforced, they should leave auto_mode off."""
    gate = PermissionGate(config=PermissionGateConfig(
        shell_deny_patterns=["rm -rf"],
    ))
    gate.auto_mode = True
    decision = await gate.check(
        "shell", {"command": "rm -rf /etc"}, session_id="test"
    )
    assert decision.allow is True
    assert decision.source == "auto-mode"


@pytest.mark.asyncio
async def test_auto_mode_off_default_falls_through_to_existing_layers():
    """Default behavior (auto_mode = False) must be unchanged — no
    accidental opt-in."""
    gate = PermissionGate()
    assert gate.auto_mode is False
    # default-allow categories still allow without responder
    decision = await gate.check(
        "read_file", {"path": "C:/temp/foo.txt"}, session_id="test"
    )
    assert decision.allow is True
    assert decision.source == "default-allow"


# ---------------------------------------------------------------------------
# auto_mode toggle is per-instance, not class-level
# ---------------------------------------------------------------------------


def test_auto_mode_state_is_isolated_between_instances():
    a = PermissionGate()
    b = PermissionGate()
    a.auto_mode = True
    assert b.auto_mode is False  # not infected


# ---------------------------------------------------------------------------
# Voice TTS prompt — fires when current_source = "voice" + popup branches
# ---------------------------------------------------------------------------


class _RecordingTTS:
    def __init__(self):
        self.calls: list[str] = []

    async def synthesize(self, text: str):
        self.calls.append(text)


class _ImmediateAllowResponder:
    def __init__(self):
        self.received_count = 0

    async def __call__(self, request):
        self.received_count += 1
        return PermissionResponse(
            request_id=request.request_id, decision="allow"
        )


@pytest.mark.asyncio
async def test_voice_source_triggers_tts_prompt():
    gate = PermissionGate()
    tts = _RecordingTTS()
    gate.set_tts_engine(tts)
    gate.set_responder(_ImmediateAllowResponder())
    gate.current_source = "voice"
    # Use a non-default-allow category so it actually goes through
    # the prompt path. shell is the obvious choice.
    decision = await gate.check(
        "shell", {"command": "echo hi"}, session_id="test"
    )
    assert decision.allow is True
    # Give the TTS task a moment to run (it's fired with create_task)
    await asyncio.sleep(0.05)
    assert len(tts.calls) == 1
    assert "请点击" in tts.calls[0]


@pytest.mark.asyncio
async def test_text_source_does_not_trigger_tts_prompt():
    gate = PermissionGate()
    tts = _RecordingTTS()
    gate.set_tts_engine(tts)
    gate.set_responder(_ImmediateAllowResponder())
    # current_source is None or "text" (default)
    await gate.check(
        "shell", {"command": "echo hi"}, session_id="test"
    )
    await asyncio.sleep(0.05)
    assert tts.calls == []


@pytest.mark.asyncio
async def test_voice_source_with_no_tts_engine_still_works():
    """Permission flow must not break if TTS isn't wired."""
    gate = PermissionGate()
    gate.set_responder(_ImmediateAllowResponder())
    gate.current_source = "voice"
    # No set_tts_engine called.
    decision = await gate.check(
        "shell", {"command": "echo"}, session_id="test"
    )
    assert decision.allow is True


@pytest.mark.asyncio
async def test_tts_failure_does_not_block_permission():
    """If TTS raises, the popup still goes through."""

    class _FailingTTS:
        async def synthesize(self, text: str):
            raise RuntimeError("synthesizer down")

    gate = PermissionGate()
    gate.set_tts_engine(_FailingTTS())
    gate.set_responder(_ImmediateAllowResponder())
    gate.current_source = "voice"
    decision = await gate.check(
        "shell", {"command": "echo"}, session_id="test"
    )
    assert decision.allow is True
