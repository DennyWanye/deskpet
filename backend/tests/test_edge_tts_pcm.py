"""Task 9 (P2-2-M2): EdgeTTSProvider.synthesize_pcm_stream —

ffmpeg subprocess MP3 → s16le 24kHz mono, sliced into fixed-size PCM
chunks. Tests run against real ffmpeg (scripts/setup_ffmpeg.ps1
落到 backend/bin/ffmpeg.exe; 走 resolve_ffmpeg_path()) —— 若环境里
完全没有 ffmpeg 则整文件 skip。
"""
from __future__ import annotations

import asyncio

import pytest

from providers.edge_tts_provider import (
    EdgeTTSProvider,
    PCM_CHUNK_BYTES,
    PCM_CHUNK_SAMPLES,
    resolve_ffmpeg_path,
)


def _ffmpeg_available() -> bool:
    try:
        return resolve_ffmpeg_path() is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _ffmpeg_available(),
    reason="ffmpeg not available — run scripts/setup_ffmpeg.ps1",
)


def test_pcm_chunk_constants():
    """24kHz * 170ms ≈ 4096 samples; 16-bit mono → 8192 bytes."""
    assert PCM_CHUNK_SAMPLES == 4096
    assert PCM_CHUNK_BYTES == PCM_CHUNK_SAMPLES * 2 == 8192


@pytest.mark.asyncio
async def test_synthesize_pcm_stream_produces_pcm_chunks():
    """End-to-end: edge-tts (real) → ffmpeg pipe → PCM16 24kHz chunks.

    Needs network (edge-tts hits Microsoft) + real ffmpeg. Short text
    keeps this under 3 seconds.
    """
    provider = EdgeTTSProvider()
    chunks: list[bytes] = []
    async for chunk in provider.synthesize_pcm_stream("你好"):
        assert isinstance(chunk, (bytes, bytearray))
        # 所有完整 chunk 恒等于 PCM_CHUNK_BYTES；只有最后 padding 后也是
        # PCM_CHUNK_BYTES —— 实现保证尾部 pad 成整块。
        assert len(chunk) == PCM_CHUNK_BYTES
        chunks.append(chunk)
        if len(chunks) >= 20:  # 安全闸
            break

    assert chunks, "expected at least one PCM chunk from 你好"
    total_samples = len(chunks) * PCM_CHUNK_SAMPLES
    # "你好" at 24kHz 通常 0.4-1.0s —— 断言 >100ms 够严格又不过严。
    assert total_samples >= 24000 * 0.1, f"only {total_samples} samples"


@pytest.mark.asyncio
async def test_synthesize_pcm_stream_empty_mp3_produces_no_chunks(monkeypatch):
    """Feed 空 MP3 stream 进 pipe —— ffmpeg 解不出 PCM，生成 0 chunk
    且不挂 / 不抛。"""

    async def _empty():
        if False:
            yield b""  # pragma: no cover — typing hint only

    provider = EdgeTTSProvider()
    monkeypatch.setattr(provider, "synthesize_stream", _empty)

    chunks = []
    # 如果实现泄漏 ffmpeg 子进程就会在这里超时。
    async with asyncio.timeout(10):
        async for chunk in provider.synthesize_pcm_stream("ignored"):
            chunks.append(chunk)

    assert chunks == []
