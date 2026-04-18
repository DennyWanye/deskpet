"""Task 10 (P2-2-M2): VoicePipeline TTS 走 synthesize_pcm_stream 的端
到端契约测试。

覆盖：
  - 二进制帧头 = 0x01（PCM），长度 = 1 + PCM_CHUNK_BYTES
  - lip_sync amplitude 由 PCM16 RMS 算出，对静音/响亮两种 chunk
    产出明显不同
  - interrupted 标志生效时立刻停止发送
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

import numpy as np
import pytest

from pipeline.voice_pipeline import VoicePipeline
from providers.edge_tts_provider import PCM_CHUNK_BYTES


# ---- fakes ----


class _FakeWS:
    """最简 WS —— 只记录 send_bytes / send_json 的流水。"""

    def __init__(self):
        self.binary_frames: list[bytes] = []
        self.json_frames: list[dict] = []

    async def send_bytes(self, data: bytes) -> None:
        self.binary_frames.append(bytes(data))

    async def send_json(self, data: dict) -> None:
        self.json_frames.append(data)


class _FakeASR:
    def __init__(self, text: str):
        self._text = text

    async def transcribe(self, audio: bytes) -> str:
        return self._text


class _FakeAgent:
    def __init__(self, reply: str):
        self._reply = reply
        # 不是真的 cloud/local provider，但 transcript provider probe 会
        # 尝试读 _cloud/_local/_llm —— 都没有就不填 provider 字段，OK。

    async def chat_stream(self, messages, *, session_id: str):
        # 一次性返回全文，省掉逐 token 的复杂度
        yield self._reply


class _FakePCMTTS:
    """按脚本输出一批 PCM chunk，每块都是定长 PCM_CHUNK_BYTES。"""

    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks
        self.call_count = 0

    async def synthesize_pcm_stream(self, text: str) -> AsyncIterator[bytes]:
        self.call_count += 1
        for c in self._chunks:
            yield c

    # 旧 API 保留，不该被 M2 分支调用
    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:  # pragma: no cover
        raise AssertionError("pipeline should call synthesize_pcm_stream in M2")


class _FakeVAD:
    def on_tts_start(self): pass
    def on_tts_end(self): pass


def _silent_chunk() -> bytes:
    return b"\x00" * PCM_CHUNK_BYTES


def _loud_chunk() -> bytes:
    # 振幅 ~16000 的正弦 @ 440Hz
    t = np.arange(PCM_CHUNK_BYTES // 2, dtype=np.float32)
    wave = (16000 * np.sin(2 * np.pi * 440 * t / 24000)).astype(np.int16)
    return wave.tobytes()


# ---- tests ----


@pytest.mark.asyncio
async def test_tts_emits_pcm_frames_with_0x01_header():
    ws = _FakeWS()
    control = _FakeWS()
    tts = _FakePCMTTS([_loud_chunk(), _loud_chunk()])
    pipe = VoicePipeline(
        vad=_FakeVAD(),
        asr=_FakeASR("你好"),
        agent=_FakeAgent("嗨"),
        tts=tts,
        control_ws=control,
    )

    await pipe._process_utterance(b"fake-pcm-in", ws)

    # 应有 2 个二进制帧（两块 PCM）
    assert len(ws.binary_frames) == 2
    for frame in ws.binary_frames:
        assert frame[0:1] == b"\x01", f"expected PCM header 0x01, got {frame[0:1]!r}"
        assert len(frame) == 1 + PCM_CHUNK_BYTES == 8193


@pytest.mark.asyncio
async def test_lip_sync_amplitude_reflects_pcm_rms():
    ws = _FakeWS()
    control = _FakeWS()
    tts = _FakePCMTTS([_silent_chunk(), _loud_chunk()])
    pipe = VoicePipeline(
        vad=_FakeVAD(),
        asr=_FakeASR("x"),
        agent=_FakeAgent("y"),
        tts=tts,
        control_ws=control,
    )

    await pipe._process_utterance(b"fake-pcm", ws)

    lip = [m for m in control.json_frames if m.get("type") == "lip_sync"]
    assert len(lip) == 2

    # 静音块 → amplitude≈0；响亮块 → amplitude 明显 > 静音
    amp_silent = lip[0]["payload"]["amplitude"]
    amp_loud = lip[1]["payload"]["amplitude"]
    assert amp_silent < 0.05, f"silent chunk amplitude={amp_silent} too high"
    assert amp_loud > amp_silent + 0.3, (
        f"loud ({amp_loud}) should dominate silent ({amp_silent})"
    )
    assert 0.0 <= amp_loud <= 1.0


@pytest.mark.asyncio
async def test_tts_honors_interrupt_mid_stream():
    ws = _FakeWS()
    control = _FakeWS()

    # TTS 慢慢吐块 —— 给打断窗口
    class _SlowTTS(_FakePCMTTS):
        async def synthesize_pcm_stream(self, text):
            for c in self._chunks:
                await asyncio.sleep(0.02)
                yield c

    tts = _SlowTTS([_loud_chunk()] * 5)
    pipe = VoicePipeline(
        vad=_FakeVAD(),
        asr=_FakeASR("x"),
        agent=_FakeAgent("y"),
        tts=tts,
        control_ws=control,
    )

    async def _interrupter():
        await asyncio.sleep(0.05)
        pipe._interrupted = True

    await asyncio.gather(
        pipe._process_utterance(b"fake", ws),
        _interrupter(),
    )

    # 不应把全部 5 块发完
    assert len(ws.binary_frames) < 5
