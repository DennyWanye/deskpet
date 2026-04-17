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

        P2-2 hot-fix (2026-04-17): Chinese-only product, so we lock
        language="zh" to eliminate the pt/es/en/fr drift Whisper does on
        short clips. vad_filter=True adds Whisper's own VAD as a second
        pass — Silero upstream catches speech boundaries, this filters
        out lingering noise/echo inside the clip that silero can't see.
        initial_prompt primes the decoder toward Mandarin conversation
        vocabulary and substantially reduces the "Thank you / Gracias /
        Au revoir" training-data hallucinations on short/low-energy input.
        """
        if self._model is None:
            await self.load()

        audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        segments, info = self._model.transcribe(
            audio_np,
            language="zh",
            beam_size=8,
            best_of=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            # 引导 decoder 偏向日常对话/提问/聊天/讲笑话 的词汇分布，
            # 缓解"讲→赞 / 笑话→小话"这类中文同声调域混淆。
            initial_prompt=(
                "以下是用户与桌面 AI 助手的普通话对话。"
                "场景：闲聊、提问、讲笑话、请求帮助。"
            ),
            # 关闭把上一段识别结果当下一段 prompt 的机制 ——
            # 连续识别时会把早先的错误串联下去（典型"错误传染"）。
            condition_on_previous_text=False,
            # 关闭 temperature fallback，让输出稳定可复现。
            temperature=0.0,
            # 提高"无语音"判定门槛，减少把纯噪音/底噪识别成短词的幻觉。
            no_speech_threshold=0.6,
        )

        text = " ".join(seg.text.strip() for seg in segments)
        logger.info(
            "asr_result",
            text=text,
            language=info.language,
            duration=f"{info.duration:.1f}s",
        )
        return text
