"""S9 tests: CosyVoice 2 provider — fallback path is the only reachable
branch on CI (no GPU, no cosyvoice package), so we test that.

Real-weights synthesis is covered manually via ``scripts/tts_smoke.py``
when a developer has the env set up; it's gated behind pragma: no cover
in the provider.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from providers.cosyvoice_tts import CosyVoice2Provider


@pytest.mark.asyncio
async def test_missing_weights_falls_back_to_edge(tmp_path: Path):
    """Empty model_dir → fallback path activates, backend reports 'edge-tts'."""
    provider = CosyVoice2Provider(
        model_dir=str(tmp_path),  # empty — no llm.pt/flow.pt/hift.pt
        fallback_voice="zh-CN-XiaoyiNeural",
    )
    assert provider.active_backend == "unloaded"
    await provider.load()
    assert provider.active_backend == "edge-tts"
    # audio metadata must reflect the fallback, not the cosyvoice defaults
    assert provider.audio_format == "mp3"


@pytest.mark.asyncio
async def test_partial_weights_still_falls_back(tmp_path: Path):
    """Only one of three weights present → still fallback (fail-closed)."""
    (tmp_path / "llm.pt").write_bytes(b"not a real model")
    # flow.pt and hift.pt missing
    provider = CosyVoice2Provider(model_dir=str(tmp_path))
    await provider.load()
    assert provider.active_backend == "edge-tts"


@pytest.mark.asyncio
async def test_stream_yields_at_least_one_chunk_via_fallback(
    tmp_path: Path, monkeypatch
):
    """synthesize_stream must delegate to the fallback and yield bytes."""
    provider = CosyVoice2Provider(model_dir=str(tmp_path))
    await provider.load()

    # Patch the edge backend's synth to avoid hitting the network.
    async def fake_stream(self, text):  # noqa: ARG001
        yield b"FAKEMP3CHUNK"

    from providers.edge_tts_provider import EdgeTTSProvider

    monkeypatch.setattr(EdgeTTSProvider, "synthesize_stream", fake_stream)

    chunks = []
    async for c in provider.synthesize_stream("你好"):
        chunks.append(c)
    assert chunks == [b"FAKEMP3CHUNK"]


@pytest.mark.asyncio
async def test_synthesize_delegates_to_fallback(tmp_path: Path, monkeypatch):
    """Full-utterance synthesize routes through edge-tts when unavailable."""
    provider = CosyVoice2Provider(model_dir=str(tmp_path))
    await provider.load()

    async def fake_synth(self, text):  # noqa: ARG001
        return b"FAKEMP3"

    from providers.edge_tts_provider import EdgeTTSProvider

    monkeypatch.setattr(EdgeTTSProvider, "synthesize", fake_synth)

    out = await provider.synthesize("测试")
    assert out == b"FAKEMP3"


def test_active_backend_reports_unloaded_before_load(tmp_path: Path):
    """Before load() is called, neither backend is active."""
    provider = CosyVoice2Provider(model_dir=str(tmp_path))
    assert provider.active_backend == "unloaded"
