"""edge-tts provider — Microsoft Edge TTS as Phase 1 fallback."""
from __future__ import annotations

from typing import AsyncIterator

import edge_tts
import structlog

logger = structlog.get_logger()

DEFAULT_VOICE = "zh-CN-XiaoyiNeural"


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
