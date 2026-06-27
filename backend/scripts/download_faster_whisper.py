# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Download faster-whisper-large-v3-turbo INT8 weights into the per-user models dir.

P4-S20+ install bundle Plan A: the NSIS installer ships a thin backend
(~1.5 GB) without ASR weights. This script populates
``%LocalAppData%/deskpet/models/faster-whisper-large-v3-turbo/`` so the
backend can resolve them via ``paths.resolve_model_dir``.

Why mobiuslabsgmbh/faster-whisper-large-v3-turbo?
  * Already in CTranslate2 INT8 format (drop-in for faster-whisper)
  * ~1.5 GB on disk vs 2.7 GB for the official Systran fp16 release
  * Same accuracy on Chinese ASR within rounding error per our tests

Usage:
    python -m scripts.download_faster_whisper                # real download (~1.5 GB)
    python -m scripts.download_faster_whisper --dry-run      # print path only
    python -m scripts.download_faster_whisper --force        # overwrite existing
    python -m scripts.download_faster_whisper --mirror hf-mirror  # use Chinese mirror

The ``huggingface_hub`` dependency is imported lazily so ``--dry-run``
works without it (download path still requires it; install via
``pip install huggingface_hub``).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ID = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
SUBDIR = "faster-whisper-large-v3-turbo"

# Files we actually need at runtime — anything else (LICENSE, README, etc.)
# is filtered to keep the on-disk footprint clean. faster-whisper's
# WhisperModel constructor needs all of these to be present.
ALLOW_PATTERNS = [
    "config.json",
    "model.bin",
    "preprocessor_config.json",
    "tokenizer.json",
    "vocabulary.json",
]

MIRRORS = {
    "default": None,  # huggingface.co (auto)
    "hf-mirror": "https://hf-mirror.com",  # Chinese community mirror
}


def _resolve_target_dir() -> Path:
    """Match ``backend/paths.py``'s ``user_models_dir() / SUBDIR``."""
    try:
        import platformdirs
    except ImportError:
        sys.stderr.write(
            "platformdirs not installed. Run `pip install platformdirs` first.\n"
        )
        sys.exit(2)
    base = Path(platformdirs.user_data_dir("deskpet", appauthor=False, roaming=False))
    return (base / "models" / SUBDIR).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print target path and exit.")
    parser.add_argument(
        "--force", action="store_true",
        help="Re-download even if target dir already has files."
    )
    parser.add_argument(
        "--mirror", choices=list(MIRRORS), default="default",
        help="Use a specific HuggingFace mirror (hf-mirror = Chinese mirror).",
    )
    args = parser.parse_args()

    target = _resolve_target_dir()
    print(f"target: {target}")

    if args.dry_run:
        print(f"would download: {REPO_ID} -> {target}")
        return 0

    if target.exists() and any(target.iterdir()) and not args.force:
        # Quick sanity check: if model.bin is there, assume the download
        # completed previously. Use --force to redo.
        if (target / "model.bin").is_file():
            print(f"already populated: {target / 'model.bin'} exists.")
            print("Use --force to redo download.")
            return 0

    target.mkdir(parents=True, exist_ok=True)

    if MIRRORS[args.mirror]:
        os.environ["HF_ENDPOINT"] = MIRRORS[args.mirror]
        print(f"using mirror: {MIRRORS[args.mirror]}")

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        sys.stderr.write(
            "huggingface_hub not installed. Run `pip install huggingface_hub` first.\n"
        )
        return 2

    print(f"downloading {REPO_ID} (~1.5 GB) ...")
    snapshot_download(
        repo_id=REPO_ID,
        local_dir=str(target),
        allow_patterns=ALLOW_PATTERNS,
        # Without local_dir_use_symlinks=False, HF caches in ~/.cache/huggingface
        # and the local_dir holds symlinks. We want real files at the target so
        # uninstalling deskpet cleans up everything.
        local_dir_use_symlinks=False,
    )
    print(f"done. files at: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
