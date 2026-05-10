"""One-shot model setup for fresh deskpet installs.

After installing the thin NSIS bundle, run this once to populate
``%LocalAppData%/deskpet/models/`` with BGE-M3 (vector embedder) and
faster-whisper-large-v3-turbo (ASR). Without these:

  * BGE-M3 missing  → vector recall (long-term memory) degrades to mock
                      vectors. Chat still works, recall accuracy drops.
  * Whisper missing → faster-whisper falls back to HuggingFace runtime
                      auto-download on first transcribe (5 GB official
                      fp16 build, slower than the INT8 version).

Usage::

    # From a frozen install (Windows):
    "C:\\Program Files\\DeskPet\\backend\\deskpet-backend.exe" setup-models

    # From a dev checkout:
    python -m scripts.setup_models                # both models
    python -m scripts.setup_models --only bge-m3  # one only
    python -m scripts.setup_models --mirror hf-mirror  # Chinese mirror

Total download: ~2.6 GB (1.1 GB BGE-M3 fp16 effective + 1.5 GB whisper).
The HuggingFace ``snapshot_download`` resumes interrupted transfers, so
the script is safe to re-run.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only", choices=["bge-m3", "whisper"], default=None,
        help="Download only one model (default: both).",
    )
    parser.add_argument(
        "--mirror", default="default",
        help="HF mirror name (default | hf-mirror); see download scripts.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-download even if target dirs are already populated.",
    )
    args = parser.parse_args()

    # Make `from scripts.X import ...` work whether invoked as
    # `python -m scripts.setup_models` (already on path) or via the
    # frozen exe entrypoint.
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))

    rc = 0
    targets = []
    if args.only != "whisper":
        targets.append("bge-m3")
    if args.only != "bge-m3":
        targets.append("whisper")

    for name in targets:
        print(f"\n=== {name} ===")
        sys.argv = [name]
        if args.force:
            sys.argv.append("--force")
        if name == "bge-m3":
            from download_bge_m3 import main as run
            rc = run() or rc
        elif name == "whisper":
            sys.argv.append("--mirror")
            sys.argv.append(args.mirror)
            from download_faster_whisper import main as run
            rc = run() or rc

    if rc == 0:
        print("\nAll requested models installed. Restart deskpet to pick them up.")
    else:
        print(f"\nSetup finished with errors (exit={rc}).", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
