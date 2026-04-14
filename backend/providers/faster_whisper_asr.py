"""faster-whisper ASR provider — local CUDA inference."""
from __future__ import annotations

import numpy as np
import structlog
from faster_whisper import WhisperModel

logger = structlog.get_logger()


class FasterWhisperASR:
    """
    Implements ASRProvider protocol.
    Loads faster-whisper model from local directory or HuggingFace cache.
    Uses CTranslate2 backend for fast CUDA inference.
    """

    def __init__(
        self,
        model: str = "large-v3-turbo",
        device: str = "cuda",
        compute_type: str = "float16",
        local_dir: str | None = None,
    ):
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self.local_dir = local_dir
        self._model: WhisperModel | None = None

    async def load(self) -> None:
        if self._model is not None:
            return
        model_path = self.local_dir if self.local_dir else self.model_name
        logger.info(
            "loading faster-whisper",
            model=model_path,
            device=self.device,
            compute_type=self.compute_type,
        )
        self._model = WhisperModel(
            model_path,
            device=self.device,
            compute_type=self.compute_type,
        )
        logger.info("faster-whisper loaded")

    async def transcribe(self, audio_bytes: bytes) -> str:
        """
        Transcribe 16kHz int16 PCM bytes to text.
        Auto-detects language (supports Chinese + English).
        """
        if self._model is None:
            await self.load()

        audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        segments, info = self._model.transcribe(
            audio_np,
            language=None,  # auto-detect
            beam_size=5,
            vad_filter=False,  # we have silero-vad upstream
        )

        text = " ".join(seg.text.strip() for seg in segments)
        logger.info(
            "asr_result",
            text=text,
            language=info.language,
            duration=f"{info.duration:.1f}s",
        )
        return text
