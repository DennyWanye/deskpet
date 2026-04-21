#!/usr/bin/env python3
"""P2-2-F1 ASR accuracy bench — short-audio hotwords + padding.

Compares two FasterWhisperASR configs against a folder of .wav samples
whose filename (sans extension) is the ground-truth transcript:

    scripts/perf/asr_samples/
        讲个笑话.wav
        你好.wav
        今天天气怎么样.wav
        ...

Two passes per sample:
  - baseline  : no hotwords, no padding  (old P2-2-M3 behaviour)
  - improved  : hotwords from config.toml + 300ms silence pad

Reports char-level WER (edit distance / len(reference)) per sample +
aggregate. Exit 0 iff improved WER <= baseline WER.

**Not in CI**: this script needs CUDA + ~1.6GB model. Runs manually after
recording fresh samples. Samples dir is .gitignored (each tester records
their own voice).

Usage:
    python scripts/perf/asr_accuracy.py
    python scripts/perf/asr_accuracy.py --samples-dir path/to/wavs
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import wave
from pathlib import Path

# Make `backend/` importable regardless of where the script is launched.
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "backend"))

from config import load_config  # noqa: E402
from providers.faster_whisper_asr import FasterWhisperASR  # noqa: E402


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein distance. Small strings, so the quadratic impl is fine."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr.append(min(
                prev[j] + 1,       # deletion
                curr[j - 1] + 1,   # insertion
                prev[j - 1] + cost # substitution
            ))
        prev = curr
    return prev[-1]


def _wer(reference: str, hypothesis: str) -> float:
    """Character-level WER. Returns 0.0 for empty reference (no signal)."""
    if not reference:
        return 0.0
    return _edit_distance(reference, hypothesis) / len(reference)


def _read_wav_to_int16_bytes(path: Path) -> bytes:
    """Read a mono 16kHz int16 WAV and return the raw PCM bytes.

    If the file has different sample rate / width / channels, print a
    warning but still return the raw bytes — the user can re-record.
    """
    with wave.open(str(path), "rb") as wav:
        sr = wav.getframerate()
        w = wav.getsampwidth()
        ch = wav.getnchannels()
        if (sr, w, ch) != (16000, 2, 1):
            print(
                f"  [warn] {path.name}: expected 16kHz/16bit/mono, "
                f"got {sr}Hz/{w*8}bit/{ch}ch — results may be off"
            )
        return wav.readframes(wav.getnframes())


async def _transcribe_all(asr: FasterWhisperASR, samples: list[tuple[str, bytes]]) -> list[str]:
    """Run the ASR over each sample sequentially (pipeline is GPU-serial)."""
    outs: list[str] = []
    for _, pcm in samples:
        text = await asr.transcribe(pcm)
        outs.append(text.strip())
    return outs


def _print_table(label: str, refs: list[str], hyps: list[str]) -> float:
    total = 0.0
    print(f"\n[{label}]")
    print(f"  {'reference':<20}{'hypothesis':<30}{'WER':>8}")
    print(f"  {'-'*20}{'-'*30}{'-'*8}")
    for ref, hyp in zip(refs, hyps):
        w = _wer(ref, hyp)
        total += w
        print(f"  {ref:<20}{hyp:<30}{w:>7.2f}")
    avg = total / max(1, len(refs))
    print(f"  {'mean WER':<50}{avg:>7.2f}")
    return avg


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--samples-dir",
        type=Path,
        default=_ROOT / "scripts" / "perf" / "asr_samples",
        help="Directory of reference_text.wav files",
    )
    ap.add_argument("--config", type=Path, default=_ROOT / "config.toml")
    args = ap.parse_args()

    if not args.samples_dir.exists():
        print(f"samples dir not found: {args.samples_dir}")
        print("Record some .wav files (mono/16kHz/int16) named after the")
        print("spoken text, e.g. `讲个笑话.wav`, and re-run.")
        return 0  # not a failure — just no signal

    wavs = sorted(args.samples_dir.glob("*.wav"))
    if not wavs:
        print(f"no .wav files in {args.samples_dir}")
        return 0

    print(f"Loading {len(wavs)} samples from {args.samples_dir}")
    samples = [(p.stem, _read_wav_to_int16_bytes(p)) for p in wavs]
    refs = [s[0] for s in samples]

    cfg = load_config(args.config)
    local_model_dir = _ROOT / "backend" / "assets" / "faster-whisper-large-v3-turbo"
    local_dir = str(local_model_dir) if local_model_dir.exists() else None

    print("\nBuilding baseline ASR (no hotwords, no padding)...")
    # Build a baseline by subclassing to disable the new behaviour.
    class _BaselineASR(FasterWhisperASR):
        async def transcribe(self, audio_bytes: bytes) -> str:  # type: ignore[override]
            import numpy as np
            if self._model is None:
                await self.load()
            audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype("float32") / 32768.0
            segments, _info = self._model.transcribe(
                audio_np,
                language="zh",
                beam_size=8,
                best_of=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                condition_on_previous_text=False,
                temperature=0.0,
                no_speech_threshold=0.4,
            )
            return " ".join(s.text.strip() for s in segments)

    baseline = _BaselineASR(
        model=cfg.asr.model, device=cfg.asr.device,
        compute_type=cfg.asr.compute_type, local_dir=local_dir,
    )
    improved = FasterWhisperASR(
        model=cfg.asr.model, device=cfg.asr.device,
        compute_type=cfg.asr.compute_type, local_dir=local_dir,
        hotwords=cfg.asr.hotwords,
    )

    print("Running baseline pass...")
    base_out = await _transcribe_all(baseline, samples)
    print("Running improved pass...")
    impr_out = await _transcribe_all(improved, samples)

    base_wer = _print_table("BASELINE (no hotwords, no pad)", refs, base_out)
    impr_wer = _print_table("IMPROVED (hotwords + pad)", refs, impr_out)

    print("\n" + "=" * 64)
    print(f"Baseline mean WER: {base_wer:.3f}")
    print(f"Improved mean WER: {impr_wer:.3f}")
    print(f"Delta:             {base_wer - impr_wer:+.3f}")
    print("=" * 64)
    return 0 if impr_wer <= base_wer else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
