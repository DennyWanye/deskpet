"""Download the BGE-M3 embedding weights into the per-user models directory.

P4-S0, task 1.3. Target layout:

    Windows: %LocalAppData%\\deskpet\\models\\bge-m3-int8\\
    macOS/Linux: ~/.local/share/deskpet/models/bge-m3-int8/

Why a separate script (not runtime lazy-download)?
  * Weights are ~1-2 GB (INT8 quantised target ~286 MB, full fp32 ~2.2 GB).
    We want the download to be explicit — users pick the right network +
    disk, not a silent first-run surprise.
  * The backend itself must never hit HuggingFace at startup; it only maps
    the already-on-disk folder via ``user_models_dir() / "bge-m3-int8"``.

Notes on INT8:
  * HuggingFace's official BAAI/bge-m3 repo does NOT currently ship an
    INT8 variant. We pull the full precision repo and rely on P4-S2's
    ``embedder.py`` to quantise at load time (FlagEmbedding supports this
    via ``use_fp16=True`` / manual bitsandbytes; exact strategy is decided
    in P4-S2). The subfolder name remains ``bge-m3-int8`` per config.toml
    so the runtime path stays stable — this script documents that the
    files inside may initially be fp16 + quantised on load.

Usage:
    python backend/scripts/download_bge_m3.py --dry-run   # prints path only
    python backend/scripts/download_bge_m3.py             # real download
    python backend/scripts/download_bge_m3.py --force     # overwrite existing

The ``huggingface_hub`` dependency is NOT in the backend runtime deps
(installing it pulls tqdm + lots of transient bits). It is imported
lazily here so ``--dry-run`` works with just the stdlib + platformdirs.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# --- Path resolution ---------------------------------------------------------

REPO_ID = "BAAI/bge-m3"
SUBDIR = "bge-m3-int8"


def _resolve_target_dir() -> Path:
    """Return the on-disk target folder under the user models dir.

    Uses platformdirs (already a backend runtime dep) to pick the right
    OS-specific non-roaming app data dir, matching ``backend/paths.py``'s
    ``user_models_dir()`` semantics. We don't import ``backend.paths``
    here because this script lives under ``backend/scripts/`` and we want
    it to be runnable both in dev and from a partial checkout without
    fiddling with sys.path.
    """
    override = os.environ.get("DESKPET_MODEL_ROOT")
    if override:
        return (Path(override) / SUBDIR).resolve()

    try:
        import platformdirs
    except ModuleNotFoundError as exc:
        print(
            "[download_bge_m3] platformdirs is required (already a backend dep).\n"
            "  pip install platformdirs\n"
            f"  original error: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    local_base = Path(
        platformdirs.user_data_dir("deskpet", appauthor=False, roaming=False)
    )
    return (local_base / "models" / SUBDIR).resolve()


# --- Main --------------------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Download BGE-M3 weights to the deskpet user models dir.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve the target path and print what would happen, do not download.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if the target directory already exists (overwrites).",
    )
    p.add_argument(
        "--repo-id",
        default=REPO_ID,
        help="HuggingFace repo id to download.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    target = _resolve_target_dir()

    already_exists = target.exists() and any(target.iterdir()) if target.exists() else False

    print(f"[download_bge_m3] repo id      : {args.repo_id}")
    print(f"[download_bge_m3] target dir   : {target}")
    print(f"[download_bge_m3] already pop. : {already_exists}")
    print(f"[download_bge_m3] force        : {args.force}")
    print(f"[download_bge_m3] dry-run      : {args.dry_run}")

    if args.dry_run:
        print("[download_bge_m3] dry-run: no download performed.")
        return 0

    if already_exists and not args.force:
        print(
            "[download_bge_m3] target already populated; pass --force to overwrite.",
        )
        return 0

    # Lazy import: huggingface_hub is NOT a backend runtime dep. Give a
    # clear install hint instead of an opaque ModuleNotFoundError.
    try:
        from huggingface_hub import snapshot_download
    except ModuleNotFoundError:
        print(
            "[download_bge_m3] huggingface_hub not installed.\n"
            "  pip install huggingface_hub\n"
            "  (This is a dev / first-install-only dependency, it is NOT part of"
            " the backend runtime dependencies by design.)",
            file=sys.stderr,
        )
        return 3

    target.mkdir(parents=True, exist_ok=True)

    # Avoid tqdm terminal noise in case this is piped to a log — caller
    # can still see the final "downloaded to ..." line.
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

    try:
        snapshot_download(
            repo_id=args.repo_id,
            local_dir=str(target),
            local_dir_use_symlinks=False,
        )
    except Exception as exc:  # noqa: BLE001 — surface any HF error cleanly.
        print(f"[download_bge_m3] snapshot_download failed: {exc}", file=sys.stderr)
        return 4

    print(f"[download_bge_m3] downloaded to: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
