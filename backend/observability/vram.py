"""GPU VRAM detection + ASR device/compute recommendation.

Goal: let ASRConfig.device = "auto" do the right thing without the user
knowing whether they have a GPU or how much VRAM is free at startup.

Design: never import torch at module level — it's heavy and may not
even be installed on CPU-only deployments. Defer to call site inside
try/except so every failure mode maps to vram_gb = 0.0 (→ CPU).
"""
from __future__ import annotations

from typing import Literal

AsrDevice = Literal["cuda", "cpu"]
AsrCompute = Literal["float16", "int8"]


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


def recommend_asr_device(min_gb: float = 4.0) -> tuple[AsrDevice, AsrCompute]:
    """Pick (device, compute_type) for faster-whisper based on VRAM.

    large-v3-turbo ≈ 1.5GB model + transient activations; 4GB headroom
    covers most of the Gemma-co-resident scenarios. If caller needs a
    different bar, pass min_gb.
    """
    vram = detect_vram_gb()
    if vram >= min_gb:
        return ("cuda", "float16")
    return ("cpu", "int8")
