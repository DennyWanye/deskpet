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

        P2-2 hot-fix #2 (2026-04-20): 删掉 initial_prompt —— 实测它在
        短/弱音频上反而把 prompt 里的"请求帮助"直接当输出吐出来。
        Whisper 训练集中文样本充分，language="zh" 已足够；prompt 的
        副作用大于收益。同时把 no_speech_threshold 从 0.6 降到 0.4，
        让"谢谢大家 / Thank you for watching"这类 YouTube-流训练数据
        幻觉直接被判为无语音返回空串。
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
            # 关闭把上一段识别结果当下一段 prompt 的机制 ——
            # 连续识别时会把早先的错误串联下去（典型"错误传染"）。
            condition_on_previous_text=False,
            # 关闭 temperature fallback，让输出稳定可复现。
            temperature=0.0,
            # 降低"无语音"判定门槛 —— 0.4 足够把纯噪音/低能量短片段
            # 直接判为无语音，不再回退到训练集高频短语 ("谢谢大家" /
            # "Thank you for watching" 之类)。
            no_speech_threshold=0.4,
        )

        text = " ".join(seg.text.strip() for seg in segments)
        logger.info(
            "asr_result",
            text=text,
            language=info.language,
            duration=f"{info.duration:.1f}s",
        )
        return text
