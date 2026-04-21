"""P2-2-F1: short-audio padding + hotwords bias tests.

Drives the two core changes in FasterWhisperASR:

  1. `hotwords` list passed through to WhisperModel.transcribe as a single
     space-joined string (or None when empty). Mitigates short-phrase
     pinyin-adjacent hallucinations ("讲个笑话" → "一个消化").

  2. Short audio (< 3s) gets 300ms silence padding front + back before
     being fed to the model. Gives the Whisper encoder more context
     without changing ASR semantics.

We mock WhisperModel entirely so these tests run without CUDA / the
1.6GB model file.
"""
from __future__ import annotations

import sys
import types

import numpy as np
import pytest


# Stub `faster_whisper` before importing the provider — the CUDA package
# isn't present in the test env.
if "faster_whisper" not in sys.modules:
    _stub = types.ModuleType("faster_whisper")

    class _StubWhisperModel:  # replaced at runtime by the fake fixture
        def __init__(self, *a, **kw):
            pass

        def transcribe(self, *a, **kw):
            return iter(()), None

    _stub.WhisperModel = _StubWhisperModel
    sys.modules["faster_whisper"] = _stub

from providers.faster_whisper_asr import FasterWhisperASR  # noqa: E402


# --- fake WhisperModel ---------------------------------------------------


class _FakeSegment:
    def __init__(self, text: str):
        self.text = text


class _FakeInfo:
    language = "zh"
    duration = 1.5


class _FakeWhisperModel:
    """Records the args of the most recent transcribe() call."""

    def __init__(self, *args, **kwargs):
        self.last_audio: np.ndarray | None = None
        self.last_kwargs: dict = {}

    def transcribe(self, audio, **kwargs):
        self.last_audio = audio
        self.last_kwargs = dict(kwargs)
        return iter([_FakeSegment("模拟输出")]), _FakeInfo()


@pytest.fixture
def fake_asr(monkeypatch):
    """An ASR instance with _model pre-set to a fake so load() is skipped."""
    asr = FasterWhisperASR(model="fake")
    fake = _FakeWhisperModel()
    asr._model = fake
    return asr, fake


# --- hotwords ------------------------------------------------------------


@pytest.mark.asyncio
async def test_hotwords_passed_to_model_as_joined_string(fake_asr):
    """Hotwords list → single space-joined string."""
    asr, fake = fake_asr
    asr._hotwords = ["讲个笑话", "你好", "再见"]
    # 1s of silent audio
    audio = np.zeros(16000, dtype=np.int16).tobytes()

    await asr.transcribe(audio)

    assert fake.last_kwargs.get("hotwords") == "讲个笑话 你好 再见"


@pytest.mark.asyncio
async def test_empty_hotwords_passes_none(fake_asr):
    """Empty list must NOT pass hotwords= to Whisper (avoid tokenizer edge cases)."""
    asr, fake = fake_asr
    asr._hotwords = []
    audio = np.zeros(16000, dtype=np.int16).tobytes()

    await asr.transcribe(audio)

    # Either absent from kwargs, or explicitly None — both acceptable
    assert fake.last_kwargs.get("hotwords") in (None, "")


def test_faster_whisper_constructor_accepts_hotwords():
    asr = FasterWhisperASR(model="fake", hotwords=["讲个笑话"])
    assert asr._hotwords == ["讲个笑话"]


def test_faster_whisper_constructor_hotwords_default_empty():
    asr = FasterWhisperASR(model="fake")
    assert asr._hotwords == []


# --- short-audio padding -------------------------------------------------


PAD_SAMPLES = int(16000 * 0.3)  # 300ms of 16kHz


@pytest.mark.asyncio
async def test_short_audio_is_padded_front_and_back(fake_asr):
    """0.5s audio (8000 samples) → 8000 + 2*4800 = 17600 samples after pad."""
    asr, fake = fake_asr
    audio_samples = 8000  # 0.5s @ 16kHz
    audio = np.ones(audio_samples, dtype=np.int16).tobytes()

    await asr.transcribe(audio)

    assert fake.last_audio is not None
    expected = audio_samples + 2 * PAD_SAMPLES
    assert len(fake.last_audio) == expected, (
        f"expected {expected}, got {len(fake.last_audio)}"
    )
    # Front pad should be zeros
    assert np.all(fake.last_audio[:PAD_SAMPLES] == 0.0)
    # Back pad should be zeros
    assert np.all(fake.last_audio[-PAD_SAMPLES:] == 0.0)


@pytest.mark.asyncio
async def test_long_audio_not_padded(fake_asr):
    """4s audio should pass through unmodified (threshold is 3s)."""
    asr, fake = fake_asr
    audio_samples = 64000  # 4s @ 16kHz
    audio = np.ones(audio_samples, dtype=np.int16).tobytes()

    await asr.transcribe(audio)

    assert fake.last_audio is not None
    assert len(fake.last_audio) == audio_samples


@pytest.mark.asyncio
async def test_empty_audio_does_not_crash(fake_asr):
    """Edge case: zero-length audio shouldn't blow up the pad logic."""
    asr, fake = fake_asr
    await asr.transcribe(b"")
    # Either returns empty or a mocked result — the point is no exception.


@pytest.mark.asyncio
async def test_boundary_audio_just_under_threshold_is_padded(fake_asr):
    """Audio just under 3s should still be padded."""
    asr, fake = fake_asr
    audio_samples = 16000 * 2  # 2s @ 16kHz → under 3s threshold
    audio = np.ones(audio_samples, dtype=np.int16).tobytes()

    await asr.transcribe(audio)

    assert fake.last_audio is not None
    expected = audio_samples + 2 * PAD_SAMPLES
    assert len(fake.last_audio) == expected


@pytest.mark.asyncio
async def test_boundary_audio_at_threshold_not_padded(fake_asr):
    """Audio exactly at 3s threshold should NOT be padded (< vs <=)."""
    asr, fake = fake_asr
    audio_samples = 16000 * 3  # exactly 3s
    audio = np.ones(audio_samples, dtype=np.int16).tobytes()

    await asr.transcribe(audio)

    assert fake.last_audio is not None
    # Not padded: length should equal original samples
    assert len(fake.last_audio) == audio_samples
