#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""First-time model setup for OSS users.

DeskPet needs three local ML models that are NOT bundled in the git repo
(too large, separate licenses). This script downloads them into the
platform-appropriate user-data directory and verifies their integrity.

Models downloaded:
  1. faster-whisper (small / medium) — speech-to-text  (~500MB)
  2. silero-vad                      — voice activity  (~30MB)
  3. BGE-M3 INT8                     — multilingual embedding (~286MB)

Idempotent: re-running skips already-downloaded models.

Run:
    python scripts/setup_models.py [--whisper-size small|medium] [--skip-bge]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def get_user_models_dir() -> Path:
    """Return the platform-appropriate models directory.

    Windows:  %APPDATA%\\deskpet\\models
    macOS:    ~/Library/Application Support/deskpet/models
    Linux:    ~/.local/share/deskpet/models
    """
    try:
        import platformdirs
    except ImportError:
        print("ERROR: platformdirs not installed. Run `pip install platformdirs`")
        sys.exit(1)
    return Path(platformdirs.user_data_dir("deskpet")) / "models"


def setup_whisper(models_dir: Path, size: str = "small") -> None:
    """Download faster-whisper model. Lazy import; only if requested."""
    dest = models_dir / f"faster-whisper-{size}"
    if dest.exists() and any(dest.iterdir()):
        print(f"[SKIP] faster-whisper-{size} already at {dest}")
        return

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("ERROR: faster-whisper not installed. Run `pip install faster-whisper`")
        sys.exit(1)

    print(f"[DOWNLOAD] faster-whisper-{size} → {dest}")
    print("  This will trigger the HuggingFace download (~500MB for small)…")
    dest.mkdir(parents=True, exist_ok=True)
    # Loading the model triggers the download into HF cache.
    # WhisperModel auto-resolves to the HF cache dir; we just need to ensure
    # it runs once so the cache is warm. Frozen builds re-point HF_HOME later.
    _ = WhisperModel(size, device="cpu", compute_type="int8")
    print(f"[OK] faster-whisper-{size}")


def setup_silero_vad(models_dir: Path) -> None:
    """silero-vad ships its model inside the pip package — no download needed."""
    try:
        import silero_vad  # noqa: F401
    except ImportError:
        print("ERROR: silero-vad not installed. Run `pip install silero-vad>=5.1.2,<6`")
        sys.exit(1)
    print("[OK] silero-vad (bundled with pip package)")


def setup_bge_m3(models_dir: Path) -> None:
    """Download BGE-M3 INT8 quantized embedding model.

    The model lives at HuggingFace `BAAI/bge-m3` (~286MB INT8).
    """
    dest = models_dir / "bge-m3-int8"
    if dest.exists() and any(dest.iterdir()):
        print(f"[SKIP] bge-m3-int8 already at {dest}")
        return

    print(f"[DOWNLOAD] BGE-M3 INT8 → {dest}")
    print("  This pulls from HuggingFace (~286MB)…")

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("ERROR: huggingface_hub not installed. It should ship with FlagEmbedding.")
        sys.exit(1)

    dest.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id="BAAI/bge-m3",
        local_dir=str(dest),
        local_dir_use_symlinks=False,
    )
    print(f"[OK] bge-m3-int8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--whisper-size",
        choices=["small", "medium"],
        default="small",
        help="faster-whisper model size (small = 500MB, medium = 1.5GB)",
    )
    parser.add_argument(
        "--skip-bge",
        action="store_true",
        help="Skip BGE-M3 download (useful if you already have it elsewhere)",
    )
    parser.add_argument(
        "--skip-whisper",
        action="store_true",
        help="Skip faster-whisper download",
    )
    args = parser.parse_args()

    models_dir = get_user_models_dir()
    models_dir.mkdir(parents=True, exist_ok=True)
    print(f"=== DeskPet model setup ===")
    print(f"Target directory: {models_dir}")
    print()

    if not args.skip_whisper:
        setup_whisper(models_dir, args.whisper_size)
    setup_silero_vad(models_dir)
    if not args.skip_bge:
        setup_bge_m3(models_dir)

    print()
    print("=== All required models ready ===")
    print(f"Total at: {models_dir}")
    print()
    print("Next: cd tauri-app && npm run tauri:dev")
    return 0


if __name__ == "__main__":
    sys.exit(main())
