"""Tests for P2-2-M3 Task 13: dynamic VAD threshold during TTS.

While TTS is playing, we raise the VAD threshold to reduce false
triggers from speaker echo. After TTS finishes (or is interrupted),
threshold must restore to the "normal" value — otherwise the next
utterance would be harder to detect than it should be.

Also verifies that [voice] config values flow through VoicePipeline
into BargeInFilter (cooldown_ms, min_speech_during_tts_ms).
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest

from pipeline.voice_pipeline import VoicePipeline
from providers.edge_tts_provider import PCM_CHUNK_BYTES


# ---- fakes ----


class _FakeWS:
    def __init__(self):
        self.binary_frames: list[bytes] = []
        self.json_frames: list[dict] = []

    async def send_bytes(self, data: bytes) -> None:
        self.binary_frames.append(bytes(data))

    async def send_json(self, data: dict) -> None:
        self.json_frames.append(data)


class _FakeASR:
    def __init__(self, text: str = "你好"):
        self._text = text

    async def transcribe(self, audio: bytes) -> str:
        return self._text


class _FakeAgent:
    def __init__(self, reply: str = "嗨"):
        self._reply = reply

    async def chat_stream(self, messages, *, session_id: str):
        yield self._reply


class _FakePCMTTS:
    def __init__(self, chunks: list[bytes] | None = None):
        self._chunks = chunks or [b"\x00" * PCM_CHUNK_BYTES]

    async def synthesize_pcm_stream(self, text: str) -> AsyncIterator[bytes]:
        for c in self._chunks:
            yield c


class _ThresholdTrackingVAD:
    """Records every set_threshold call so tests can assert swap/restore."""

    def __init__(self, initial_threshold: float = 0.5):
        self.threshold = initial_threshold
        self.threshold_history: list[float] = [initial_threshold]

    def set_threshold(self, value: float) -> None:
        self.threshold = value
        self.threshold_history.append(value)

    # Other VAD methods the pipeline may touch — no-op for these tests.
    def on_tts_start(self) -> None: ...
    def on_tts_end(self) -> None: ...


# ---- tests ----


@pytest.mark.asyncio
async def test_vad_threshold_raised_during_tts_and_restored_after():
    """TTS start → threshold goes to during_tts; TTS end → back to normal."""
    vad = _ThresholdTrackingVAD(initial_threshold=0.5)
    pipe = VoicePipeline(
        vad=vad,
        asr=_FakeASR(),
        agent=_FakeAgent(),
        tts=_FakePCMTTS(),
        control_ws=_FakeWS(),
        vad_threshold_during_tts=0.65,
    )

    await pipe._process_utterance(b"fake-pcm", _FakeWS())

    # History should contain: 0.5 (initial) → 0.65 (TTS start) → 0.5 (TTS end).
    assert 0.65 in vad.threshold_history, (
        f"threshold was never raised during TTS: {vad.threshold_history}"
    )
    assert vad.threshold == 0.5, (
        f"threshold not restored after TTS: {vad.threshold_history}"
    )


@pytest.mark.asyncio
async def test_vad_threshold_restored_even_on_interrupt():
    """Even if user bargers in mid-TTS, threshold must be restored."""
    vad = _ThresholdTrackingVAD(initial_threshold=0.5)

    class _SlowTTS:
        async def synthesize_pcm_stream(self, text):
            for _ in range(10):
                await asyncio.sleep(0.02)
                yield b"\x00" * PCM_CHUNK_BYTES

    pipe = VoicePipeline(
        vad=vad,
        asr=_FakeASR(),
        agent=_FakeAgent(),
        tts=_SlowTTS(),
        control_ws=_FakeWS(),
        vad_threshold_during_tts=0.65,
    )

    async def _interrupter():
        await asyncio.sleep(0.05)
        pipe._interrupted = True

    await asyncio.gather(
        pipe._process_utterance(b"fake", _FakeWS()),
        _interrupter(),
    )

    assert vad.threshold == 0.5, (
        f"threshold not restored after interrupt: {vad.threshold_history}"
    )


@pytest.mark.asyncio
async def test_vad_threshold_restored_on_exception():
    """Pipeline exceptions must also restore threshold (finally block)."""
    vad = _ThresholdTrackingVAD(initial_threshold=0.5)

    class _BrokenTTS:
        async def synthesize_pcm_stream(self, text):
            yield b"\x00" * PCM_CHUNK_BYTES
            raise RuntimeError("simulated ffmpeg crash")

    pipe = VoicePipeline(
        vad=vad,
        asr=_FakeASR(),
        agent=_FakeAgent(),
        tts=_BrokenTTS(),
        control_ws=_FakeWS(),
        vad_threshold_during_tts=0.65,
    )

    # The pipeline catches the exception internally and posts an error
    # message — we only care that the threshold was restored.
    await pipe._process_utterance(b"fake", _FakeWS())

    assert vad.threshold == 0.5, (
        f"threshold not restored after TTS error: {vad.threshold_history}"
    )


def test_pipeline_accepts_voice_config_kwargs():
    """VoicePipeline.__init__ must accept the three [voice] config kwargs."""
    vad = _ThresholdTrackingVAD()
    pipe = VoicePipeline(
        vad=vad,
        asr=_FakeASR(),
        agent=_FakeAgent(),
        tts=_FakePCMTTS(),
        control_ws=_FakeWS(),
        vad_threshold_during_tts=0.75,
        min_speech_ms_during_tts=500,
        tts_cooldown_ms=250,
    )
    # Defaults for backwards compat: omitting the kwargs must not crash.
    pipe2 = VoicePipeline(
        vad=vad,
        asr=_FakeASR(),
        agent=_FakeAgent(),
        tts=_FakePCMTTS(),
        control_ws=_FakeWS(),
    )
    assert pipe is not None and pipe2 is not None


def test_pipeline_forwards_cooldown_to_barge_in_filter():
    """tts_cooldown_ms and min_speech_ms_during_tts must flow into BargeInFilter."""
    vad = _ThresholdTrackingVAD()
    pipe = VoicePipeline(
        vad=vad,
        asr=_FakeASR(),
        agent=_FakeAgent(),
        tts=_FakePCMTTS(),
        control_ws=_FakeWS(),
        tts_cooldown_ms=250,
        min_speech_ms_during_tts=500,
    )
    # BargeInFilter exposes these as private attrs — read them to verify wiring.
    assert pipe._barge_in_filter._cooldown_ms == 250
    assert pipe._barge_in_filter._min_speech_during_tts_ms == 500


# ---- speech_start barge-in wiring fix (per-frame re-evaluation) ----


class _ScriptedVAD:
    """VAD that returns scripted events + speech durations per process_chunk call.

    Lets tests drive "sustained speech across N frames during TTS" without
    needing a real silero model.
    """

    def __init__(self, script: list[tuple[list[dict], int]]):
        """script: list of (events_to_return, duration_ms_after_chunk) tuples."""
        self._script = list(script)
        self._call = 0
        self.threshold = 0.5
        self._current_duration = 0

    def process_chunk(self, pcm_bytes: bytes) -> list[dict]:
        if self._call >= len(self._script):
            # After script exhausted, emit nothing and keep last duration.
            return []
        events, duration = self._script[self._call]
        self._call += 1
        self._current_duration = duration
        return list(events)

    def current_speech_duration_ms(self) -> int:
        return self._current_duration

    def set_threshold(self, value: float) -> None:
        self.threshold = value


@pytest.mark.asyncio
async def test_sustained_speech_during_tts_triggers_barge_in():
    """Per-frame re-evaluation: speech sustained >= min_speech_ms_during_tts
    MUST emit tts_barge_in even though speech_start event only fires once
    with duration=0. This was the M1 wiring gap M3 fixes.
    """
    # Frame 1: speech_start @ duration=0
    # Frames 2-14: still in speech, duration grows
    # Frame 15: duration=420ms (> 400ms threshold) → barge-in must fire
    script = [
        ([{"event": "speech_start"}], 0),    # frame 1
        *[([], d) for d in range(32, 420, 32)],  # frames 2..13
        ([], 420),                             # frame 14
        ([], 450),                             # frame 15 — after barge-in
    ]
    vad = _ScriptedVAD(script)
    pipe = VoicePipeline(
        vad=vad,
        asr=_FakeASR(),
        agent=_FakeAgent(),
        tts=_FakePCMTTS(),
        control_ws=_FakeWS(),
        min_speech_ms_during_tts=400,
    )
    # Simulate TTS-in-progress
    pipe._processing = True
    pipe._barge_in_filter.on_tts_start()

    ws = _FakeWS()
    for _ in range(len(script)):
        await pipe.process_audio_chunk(b"\x00" * 1024, ws)

    barge_events = [m for m in ws.json_frames if m.get("type") == "tts_barge_in"]
    assert len(barge_events) == 1, (
        f"expected exactly one tts_barge_in, got {len(barge_events)}: {ws.json_frames}"
    )
    assert pipe._interrupted is True


@pytest.mark.asyncio
async def test_short_burst_during_tts_does_not_trigger_barge_in():
    """Speech that ends before min_speech_ms_during_tts must NOT interrupt TTS."""
    # 4 frames of speech (~128ms) then speech_end — well under 400ms threshold.
    script = [
        ([{"event": "speech_start"}], 0),
        ([], 32),
        ([], 64),
        ([], 96),
        ([{"event": "speech_end", "audio": b""}], 128),
    ]
    vad = _ScriptedVAD(script)
    pipe = VoicePipeline(
        vad=vad,
        asr=_FakeASR(),
        agent=_FakeAgent(),
        tts=_FakePCMTTS(),
        control_ws=_FakeWS(),
        min_speech_ms_during_tts=400,
    )
    pipe._processing = True
    pipe._barge_in_filter.on_tts_start()

    ws = _FakeWS()
    # Only pump speech_start + 3 continuation frames. Stopping before the
    # speech_end frame avoids triggering the full ASR→agent→TTS restart
    # which this test doesn't care about.
    for _ in range(4):
        await pipe.process_audio_chunk(b"\x00" * 1024, ws)

    barge_events = [m for m in ws.json_frames if m.get("type") == "tts_barge_in"]
    assert barge_events == [], (
        f"short burst should not barge in, got: {barge_events}"
    )


@pytest.mark.asyncio
async def test_barge_in_fires_only_once_per_speech_segment():
    """Once barge-in fires, subsequent frames in the same speech segment
    must NOT re-fire (spam prevention)."""
    script = [
        ([{"event": "speech_start"}], 0),
        *[([], d) for d in range(32, 800, 32)],  # long continued speech
    ]
    vad = _ScriptedVAD(script)
    pipe = VoicePipeline(
        vad=vad,
        asr=_FakeASR(),
        agent=_FakeAgent(),
        tts=_FakePCMTTS(),
        control_ws=_FakeWS(),
        min_speech_ms_during_tts=400,
    )
    pipe._processing = True
    pipe._barge_in_filter.on_tts_start()

    ws = _FakeWS()
    for _ in range(len(script)):
        await pipe.process_audio_chunk(b"\x00" * 1024, ws)

    barge_events = [m for m in ws.json_frames if m.get("type") == "tts_barge_in"]
    assert len(barge_events) == 1, (
        f"expected exactly one barge-in, got {len(barge_events)}"
    )
