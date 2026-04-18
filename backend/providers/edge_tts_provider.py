"""edge-tts provider — Microsoft Edge TTS as Phase 1 fallback.

P2-2-M2: 新增 synthesize_pcm_stream() —— edge-tts 只出 MP3，前端实时
播放需要 PCM16 才能做抖动缓冲 / RMS 口型 / barge-in 瞬断，所以走
ffmpeg 子进程管线把 MP3 流式解成 s16le 24kHz mono。
"""
from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import AsyncIterator

import edge_tts
import structlog

logger = structlog.get_logger()

DEFAULT_VOICE = "zh-CN-XiaoyiNeural"

# 170ms @ 24kHz 每块 —— 既够短让打断延迟 < 200ms，又不至于让 WS 帧
# 头开销摊薄不了。16-bit mono → 2 bytes/sample。
PCM_CHUNK_SAMPLES = 4096
PCM_CHUNK_BYTES = PCM_CHUNK_SAMPLES * 2  # 8192

# scripts/setup_ffmpeg.ps1 的落地目录。运行时解析：env override > 仓
# 内 portable > PATH。
_REPO_FFMPEG = Path(__file__).resolve().parents[2] / "backend" / "bin" / "ffmpeg.exe"


def resolve_ffmpeg_path() -> str | None:
    """返回可执行 ffmpeg 路径；都没有就返回 None（调用方应 raise 或 skip）。"""
    override = os.environ.get("DESKPET_FFMPEG")
    if override and Path(override).is_file():
        return override
    if _REPO_FFMPEG.is_file():
        return str(_REPO_FFMPEG)
    on_path = shutil.which("ffmpeg")
    return on_path


class EdgeTTSProvider:
    """
    Implements TTSProvider protocol using edge-tts.
    Outputs MP3 audio bytes — frontend decodes via Web Audio API.

    Phase 1 TTS — will be replaced by CosyVoice 2 for full local deployment.
    """

    def __init__(self, voice: str = DEFAULT_VOICE):
        self.voice = voice
        self.sample_rate = 24000
        self.audio_format = "mp3"

    async def load(self) -> None:
        logger.info("edge-tts ready", voice=self.voice)

    async def synthesize(self, text: str) -> bytes:
        """Synthesize full text to MP3 bytes."""
        communicate = edge_tts.Communicate(text, self.voice)
        chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        result = b"".join(chunks)
        logger.info("tts_synthesized", text_len=len(text), audio_bytes=len(result))
        return result

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """Stream MP3 audio chunks as they arrive from edge-tts."""
        communicate = edge_tts.Communicate(text, self.voice)
        buffer = bytearray()
        min_chunk = 4096  # yield at least 4KB at a time for smooth playback

        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buffer.extend(chunk["data"])
                if len(buffer) >= min_chunk:
                    yield bytes(buffer)
                    buffer.clear()

        if buffer:
            yield bytes(buffer)

    async def synthesize_pcm_stream(self, text: str) -> AsyncIterator[bytes]:
        """流式返回 PCM16 24kHz mono chunks，每块 PCM_CHUNK_BYTES（尾块填零补齐）。

        内部开 ffmpeg 子进程 —— stdin 喂 edge-tts 出的 MP3，stdout 读
        s16le 24kHz。任务并发模型：
          - feeder task 把 MP3 从 synthesize_stream 灌进 ffmpeg.stdin
          - 主协程从 ffmpeg.stdout 累积 PCM，切块后 yield
          - finally 兜底：取消 feeder、kill ffmpeg、wait 回收，避免僵尸
        """
        ffmpeg = resolve_ffmpeg_path()
        if ffmpeg is None:
            raise RuntimeError(
                "ffmpeg not found — run scripts/setup_ffmpeg.ps1 or set "
                "DESKPET_FFMPEG env var."
            )

        cmd = [
            ffmpeg,
            "-hide_banner", "-loglevel", "error",
            "-i", "pipe:0",
            "-f", "s16le", "-ar", "24000", "-ac", "1",
            "pipe:1",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def _feed() -> None:
            try:
                async for mp3_chunk in self.synthesize_stream(text):
                    if proc.stdin is None or proc.stdin.is_closing():
                        break
                    proc.stdin.write(mp3_chunk)
                    await proc.stdin.drain()
            except (ConnectionResetError, BrokenPipeError):
                # ffmpeg 被打断关 stdin —— 合法退出路径，不要噪音化日志
                pass
            finally:
                if proc.stdin is not None and not proc.stdin.is_closing():
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass

        feeder = asyncio.create_task(_feed())
        pcm_buf = bytearray()
        try:
            assert proc.stdout is not None
            while True:
                data = await proc.stdout.read(PCM_CHUNK_BYTES)
                if not data:
                    break
                pcm_buf.extend(data)
                while len(pcm_buf) >= PCM_CHUNK_BYTES:
                    yield bytes(pcm_buf[:PCM_CHUNK_BYTES])
                    del pcm_buf[:PCM_CHUNK_BYTES]
            if pcm_buf:
                # 尾块填零补齐 —— 前端 jitter buffer 只认定长帧。
                pad = PCM_CHUNK_BYTES - len(pcm_buf)
                pcm_buf.extend(b"\x00" * pad)
                yield bytes(pcm_buf)
        finally:
            feeder.cancel()
            try:
                await feeder
            except (asyncio.CancelledError, Exception):
                pass
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning("ffmpeg_wait_timeout", pid=proc.pid)
