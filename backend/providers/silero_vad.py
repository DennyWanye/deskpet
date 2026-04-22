"""silero-vad v5 — streaming voice activity detection on CPU."""
from __future__ import annotations

import torch
import structlog

logger = structlog.get_logger()


class SileroVAD:
    """
    Accepts 16kHz int16 PCM frames (512 samples = 32ms each).
    Emits speech_start / speech_end events.
    Implements VADProvider protocol.
    """

    def __init__(
        self,
        threshold: float = 0.5,
        min_speech_ms: int = 250,
        min_silence_ms: int = 500,
    ):
        self.threshold = threshold
        self.min_speech_ms = min_speech_ms
        self.min_silence_ms = min_silence_ms
        self._model: torch.jit.ScriptModule | None = None
        self._reset_state()

    def _reset_state(self) -> None:
        self._is_speech = False
        self._speech_start_ms: float = 0
        self._silence_start_ms: float = 0
        self._audio_buffer = bytearray()
        self._ms_counter: float = 0

    async def load(self) -> None:
        if self._model is not None:
            return
        logger.info("loading silero-vad v5")
        # P3-S4: use the PyPI `silero-vad` package instead of
        # `torch.hub.load("snakers4/silero-vad", ...)`. The hub path
        # needs network + a writable `~/.cache/torch/hub/` which is a
        # non-starter inside a frozen PyInstaller exe. The PyPI package
        # ships the JIT model (`silero_vad/data/silero_vad.jit`) as
        # package data, so PyInstaller picks it up via
        # `collect_data_files("silero_vad")`. Model weights are the
        # same as the hub v5 release.
        from silero_vad import load_silero_vad
        self._model = load_silero_vad(onnx=False).eval()
        logger.info("silero-vad loaded")

    def reset(self) -> None:
        self._reset_state()
        if self._model is not None:
            self._model.reset_states()

    def process_chunk(self, pcm_bytes: bytes) -> list[dict]:
        """
        Process one PCM16 audio frame (512 samples, 32ms, 16kHz).
        Returns event list: [{"event": "speech_start"}, {"event": "speech_end", "audio": bytes}]
        """
        if self._model is None:
            raise RuntimeError("VAD model not loaded — call await load() first")

        events: list[dict] = []
        # bytes / 2 (int16) / 16000 (sample rate) * 1000 (ms)
        chunk_ms = len(pcm_bytes) / 2 / 16000 * 1000

        # Copy to writable buffer (silero-vad tensor must be writable)
        audio_tensor = (
            torch.frombuffer(bytearray(pcm_bytes), dtype=torch.int16).float() / 32768.0
        )
        prob = self._model(audio_tensor, 16000).item()

        # Debug: log VAD probability every ~30 chunks (~1s)
        self._debug_counter = getattr(self, "_debug_counter", 0) + 1
        if self._debug_counter % 30 == 1:
            logger.info("vad_prob", prob=round(prob, 3), is_speech=self._is_speech)

        if prob >= self.threshold:
            if not self._is_speech:
                self._speech_start_ms = self._ms_counter
                self._is_speech = True
                self._audio_buffer = bytearray()
                events.append({"event": "speech_start"})
            self._silence_start_ms = 0
            self._audio_buffer.extend(pcm_bytes)
        else:
            if self._is_speech:
                if self._silence_start_ms == 0:
                    self._silence_start_ms = self._ms_counter
                self._audio_buffer.extend(pcm_bytes)
                silence_duration = self._ms_counter - self._silence_start_ms
                speech_duration = self._ms_counter - self._speech_start_ms
                if (
                    silence_duration >= self.min_silence_ms
                    and speech_duration >= self.min_speech_ms
                ):
                    events.append({
                        "event": "speech_end",
                        "audio": bytes(self._audio_buffer),
                    })
                    self._is_speech = False
                    self._audio_buffer = bytearray()
                    self._silence_start_ms = 0

        self._ms_counter += chunk_ms
        return events

    def set_threshold(self, value: float) -> None:
        """Dynamically adjust VAD threshold (0.0–1.0)."""
        self.threshold = value

    def set_min_speech_ms(self, value: int) -> None:
        """Dynamically adjust minimum speech duration (ms)."""
        self.min_speech_ms = value

    def current_speech_duration_ms(self) -> int:
        """Return current speech duration in ms. 0 if not currently in speech."""
        if not self._is_speech:
            return 0
        return int(self._ms_counter - self._speech_start_ms)
