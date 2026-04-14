"""GPU VRAM detection + dynamic model-tier selection (V5 §4.2).

Goal: let the user plug in any GPU (or none) and have the backend pick a
working model mix automatically — no manual `config.toml` tuning for the
three-way ASR/LLM/TTS combo.

Design: never import torch at module level — it's heavy and may not
even be installed on CPU-only deployments. Defer to call site inside
try/except so every failure mode maps to vram_gb = 0.0 (→ CPU).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AsrDevice = Literal["cuda", "cpu"]
AsrCompute = Literal["float16", "int8"]

# V5 §4.2 tier names (mapped from the Chinese 档位 in the plan).
Tier = Literal["flagship", "standard", "economy", "minimal"]


@dataclass(frozen=True)
class HardwareTier:
    """Pre-computed recipe for one VRAM band.

    The concrete model names are advisory — consumers read them and decide
    whether the recommended models are available locally. If not, they fall
    back along the tier chain (flagship → standard → economy → minimal).
    """
    tier: Tier
    min_vram_gb: float
    llm_model: str
    asr_model: str
    tts_model: str
    asr_device: AsrDevice
    asr_compute: AsrCompute


# Tier table, ordered from richest to poorest. First whose ``min_vram_gb``
# is ≤ detected VRAM wins — so keep this sorted DESC.
_TIERS: tuple[HardwareTier, ...] = (
    HardwareTier(
        tier="flagship",
        min_vram_gb=35.0,
        llm_model="gemma:27b",
        asr_model="large-v3-turbo",
        tts_model="cosyvoice2",
        asr_device="cuda",
        asr_compute="float16",
    ),
    HardwareTier(
        tier="standard",
        min_vram_gb=25.0,
        llm_model="qwen2.5:14b-q6",
        asr_model="large-v3-turbo",
        tts_model="cosyvoice2",
        asr_device="cuda",
        asr_compute="float16",
    ),
    HardwareTier(
        tier="economy",
        min_vram_gb=15.0,
        llm_model="qwen2.5:14b-q4",
        asr_model="small",  # sherpa-onnx equivalent — lighter
        tts_model="melotts",
        asr_device="cuda",
        asr_compute="int8",
    ),
    HardwareTier(
        tier="minimal",
        min_vram_gb=0.0,  # fallback catch-all
        llm_model="gemma:e4b",
        asr_model="tiny",
        tts_model="edge-tts",
        asr_device="cpu",
        asr_compute="int8",
    ),
)


def detect_vram_gb() -> float:
    """Return total VRAM of device 0 in GB, or 0.0 if unavailable.

    Zero on: no torch, no CUDA, multiple failure modes we don't care
    to distinguish — caller just needs "enough" vs "not enough".
    """
    try:
        import torch  # type: ignore[import-not-found]
    except Exception:
        return 0.0
    try:
        if not torch.cuda.is_available():
            return 0.0
        props = torch.cuda.get_device_properties(0)
        # total_memory is bytes
        return float(props.total_memory) / (1024**3)
    except Exception:
        return 0.0


def classify_tier(vram_gb: float | None = None) -> HardwareTier:
    """Return the richest tier whose VRAM floor fits.

    Pass ``vram_gb`` explicitly for testing; omit to probe the live GPU.
    """
    actual = detect_vram_gb() if vram_gb is None else vram_gb
    for tier in _TIERS:
        if actual >= tier.min_vram_gb:
            return tier
    # Guaranteed unreachable — minimal.min_vram_gb = 0.0 catches everything.
    return _TIERS[-1]


def recommend_asr_device(min_gb: float = 4.0) -> tuple[AsrDevice, AsrCompute]:
    """Pick (device, compute_type) for faster-whisper based on VRAM.

    Kept for backward compatibility (S4 ASR selection). New code should
    prefer ``classify_tier`` which returns the full hardware recipe.
    large-v3-turbo ≈ 1.5GB model + transient activations; 4GB headroom
    covers most of the Gemma-co-resident scenarios. If caller needs a
    different bar, pass min_gb.
    """
    vram = detect_vram_gb()
    if vram >= min_gb:
        return ("cuda", "float16")
    return ("cpu", "int8")
