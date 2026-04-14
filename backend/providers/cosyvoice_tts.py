"""CosyVoice 2 local TTS provider with graceful edge-tts fallback (R11).

V5 §4.1 / Phase 2 goal: run a fully-local TTS stack when the machine
has enough VRAM + the cosyvoice package installed. When any precondition
fails we drop back to edge-tts so the voice pipeline keeps working — the
caller never sees the difference except in the startup log line.

Why the fallback chain lives inside the provider (not at registration
time in main.py): the availability of CosyVoice depends on runtime state
(torch CUDA, model weights, python package install). Deciding at module
import forces a restart to reconfigure; doing it inside ``load()`` means
the user can ``pip install cosyvoice`` and just restart uvicorn.

Expected weight layout (from the ModelScope bundle):

    backend/assets/cosyvoice2/
        llm.pt
        flow.pt
        hift.pt
        campplus.onnx
        speech_tokenizer_v2.onnx
        cosyvoice2.yaml
        ...

``model_dir`` points at that directory. The reference prompt audio lives
under ``asset/`` inside the bundle and is used for zero-shot cloning.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import AsyncIterator

import structlog

from providers.edge_tts_provider import EdgeTTSProvider

logger = structlog.get_logger()

# Default zero-shot prompt text. CosyVoice 2 needs both a reference
# audio clip and the text that was spoken in it. We ship neither — the
# user must drop ``prompt.wav`` + ``prompt.txt`` under ``model_dir/asset/``
# to enable real zero-shot synthesis. Otherwise we use the built-in SFT
# voice (``中文女`` is the default one shipped with the 0.5B model).
_DEFAULT_SFT_VOICE = "中文女"


class CosyVoice2Provider:
    """Local CosyVoice 2 TTS with transparent edge-tts fallback.

    Instances start in an "unloaded" state and resolve their backend
    during ``load()``. Call sites must ``await provider.load()`` before
    the first synthesize — consistent with the other providers in this
    package (FasterWhisperASR, SileroVAD).

    Attributes:
        sample_rate: output sample rate in Hz. CosyVoice 2 emits 24kHz.
        audio_format: "wav" for local, "mp3" when fallback-activated.
    """

    def __init__(
        self,
        model_dir: str,
        fallback_voice: str = "zh-CN-XiaoyiNeural",
        sft_voice: str = _DEFAULT_SFT_VOICE,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.fallback_voice = fallback_voice
        self.sft_voice = sft_voice

        # Populated in ``load()`` — one of them ends up non-None.
        self._cosy = None  # type: ignore[var-annotated]
        self._fallback: EdgeTTSProvider | None = None

        # Advisory — the audio-channel code relies on this for its
        # frontend handshake (sample rate, container format).
        self.sample_rate = 24000
        self.audio_format = "wav"

    # ---- lifecycle -------------------------------------------------

    async def load(self) -> None:
        """Try to bring up CosyVoice 2; fall back to edge-tts on any failure.

        Failure modes covered:
        - cosyvoice package not installed (ModuleNotFoundError)
        - torch / CUDA missing
        - model weights missing or corrupt
        - CosyVoice init itself raises
        """
        if self._try_load_cosyvoice():
            return
        # Any failure — use edge-tts. We don't retry cosyvoice on the
        # fly; a restart after fixing the env is the supported recovery.
        self._fallback = EdgeTTSProvider(voice=self.fallback_voice)
        await self._fallback.load()
        self.audio_format = self._fallback.audio_format
        self.sample_rate = self._fallback.sample_rate
        logger.info(
            "cosyvoice2_fallback_active",
            reason="cosyvoice2 unavailable — using edge-tts",
            fallback_voice=self.fallback_voice,
        )

    def _try_load_cosyvoice(self) -> bool:
        """Return True iff CosyVoice 2 came up cleanly."""
        # 1. Weights must be on disk. Cheapest precondition → check first.
        required = ["llm.pt", "flow.pt", "hift.pt"]
        missing = [f for f in required if not (self.model_dir / f).exists()]
        if missing:
            logger.info(
                "cosyvoice2_weights_missing",
                model_dir=str(self.model_dir),
                missing=missing,
            )
            return False

        # 2. Lazy import — the package is heavy (pulls torch, torchaudio,
        # matcha-tts, etc.) and we don't want to block backend boot on it.
        try:
            from cosyvoice.cli.cosyvoice import CosyVoice2  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover — env-dependent
            logger.info(
                "cosyvoice2_package_unavailable",
                error=str(exc),
            )
            return False

        # 3. Instantiate. Model init does disk IO + may touch CUDA. Any
        # failure (OOM, incompatible torch version, bad weights) means
        # fall back.
        try:  # pragma: no cover — runtime-dependent
            self._cosy = CosyVoice2(
                str(self.model_dir),
                load_jit=False,
                load_trt=False,
                fp16=False,
            )
            logger.info(
                "cosyvoice2_loaded",
                model_dir=str(self.model_dir),
                sft_voice=self.sft_voice,
            )
            return True
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "cosyvoice2_init_failed",
                error=str(exc),
                model_dir=str(self.model_dir),
            )
            self._cosy = None
            return False

    # ---- TTSProvider protocol --------------------------------------

    async def synthesize(self, text: str) -> bytes:
        """Full-utterance synthesis. Returns WAV bytes (local) or MP3 (fallback)."""
        if self._cosy is not None:
            return self._synthesize_local(text)
        assert self._fallback is not None, "load() must be called first"
        return await self._fallback.synthesize(text)

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """Streaming synthesis. See class docstring for chunk semantics."""
        if self._cosy is not None:
            # CosyVoice 2 supports streaming at the speech-token granularity
            # (~80ms per chunk). We collect the raw waveform and yield one
            # WAV payload to keep the frontend decode path identical to
            # non-streaming. A finer-grained streaming hook is Phase 3.
            data = self._synthesize_local(text)
            yield data
            return
        assert self._fallback is not None, "load() must be called first"
        async for chunk in self._fallback.synthesize_stream(text):
            yield chunk

    # ---- internal --------------------------------------------------

    def _synthesize_local(self, text: str) -> bytes:  # pragma: no cover — requires GPU
        """Run CosyVoice 2 inference and return WAV bytes.

        Uses the SFT (fine-tuned speaker) path — simplest mode: no prompt
        audio needed, picks a built-in voice. For zero-shot cloning the
        caller should extend this to load prompt.wav from model_dir/asset.
        """
        import torch  # local — same lazy-import principle as above
        import torchaudio

        assert self._cosy is not None
        # inference_sft returns a generator yielding dicts with key 'tts_speech'
        waveforms: list[torch.Tensor] = []
        for chunk in self._cosy.inference_sft(text, self.sft_voice, stream=False):
            waveforms.append(chunk["tts_speech"])
        if not waveforms:
            logger.warning("cosyvoice2_empty_output", text_len=len(text))
            return b""
        wav = torch.cat(waveforms, dim=1)

        buf = io.BytesIO()
        torchaudio.save(buf, wav, self._cosy.sample_rate, format="wav")
        result = buf.getvalue()
        logger.info(
            "cosyvoice2_synthesized",
            text_len=len(text),
            audio_bytes=len(result),
        )
        return result

    @property
    def active_backend(self) -> str:
        """Introspection helper — returns 'cosyvoice2' or 'edge-tts' or 'unloaded'."""
        if self._cosy is not None:
            return "cosyvoice2"
        if self._fallback is not None:
            return "edge-tts"
        return "unloaded"
