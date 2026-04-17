"""Time-domain state machine for TTS echo suppression.

Three states:
  IDLE        — no TTS active, allow any speech_start
  TTS_PLAYING — TTS synthesis in progress, require speech_duration > threshold
  COOLDOWN    — TTS just ended, block for cooldown_ms then return to IDLE
"""
from __future__ import annotations

import enum
import time


class _State(enum.Enum):
    IDLE = "idle"
    TTS_PLAYING = "tts_playing"
    COOLDOWN = "cooldown"


class BargeInFilter:
    """Decide whether a VAD speech_start should trigger a barge-in."""

    def __init__(
        self,
        cooldown_ms: int = 800,
        min_speech_during_tts_ms: int = 400,
    ) -> None:
        self._state = _State.IDLE
        self._cooldown_ms = cooldown_ms
        self._min_speech_during_tts_ms = min_speech_during_tts_ms
        self._tts_end_time: float = 0.0

    @property
    def is_tts_active(self) -> bool:
        return self._state == _State.TTS_PLAYING

    def on_tts_start(self) -> None:
        self._state = _State.TTS_PLAYING

    def on_tts_end(self) -> None:
        self._state = _State.COOLDOWN
        self._tts_end_time = time.monotonic()

    def on_interrupted(self) -> None:
        """TTS was interrupted by user — skip cooldown."""
        self._state = _State.IDLE

    def should_allow(self, speech_duration_ms: int) -> bool:
        """Return True if a VAD speech event should trigger barge-in."""
        if self._state == _State.IDLE:
            return True

        if self._state == _State.COOLDOWN:
            elapsed_ms = (time.monotonic() - self._tts_end_time) * 1000
            if elapsed_ms >= self._cooldown_ms:
                self._state = _State.IDLE
                return True
            return False

        # TTS_PLAYING — require sustained speech
        return speech_duration_ms >= self._min_speech_during_tts_ms
