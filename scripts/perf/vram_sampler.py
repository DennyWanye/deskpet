"""Sample GPU VRAM usage over time — feeds the V5 §1.1 "< 200MB/h leak" gate.

Writes CSV rows (unix_ts, device_index, used_mb, total_mb) to --out and
prints a trailing summary. Uses nvidia-smi as the source of truth so the
measurement doesn't get fooled by PyTorch's cache — if a tensor is still
alive in the process, nvidia-smi sees it.

No-GPU path: if nvidia-smi is absent we still run, emit a single row with
used_mb=0, and tell the caller to skip the leak analysis. Keeps this
script runnable on CI workers.

Usage:
    python scripts/perf/vram_sampler.py --duration 1800 --interval 10 --out vram.csv
    python scripts/perf/vram_sampler.py --duration 30 --interval 2   # smoke
"""
from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _query_nvidia_smi() -> list[tuple[int, float, float]]:
    """Return [(device_index, used_mb, total_mb), ...]. Empty on no-GPU."""
    if shutil.which("nvidia-smi") is None:
        return []
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=index,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=5.0,
        )
    except (subprocess.SubprocessError, OSError):
        return []
    rows: list[tuple[int, float, float]] = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            continue
        try:
            idx = int(parts[0])
            used = float(parts[1])
            total = float(parts[2])
        except ValueError:
            continue
        rows.append((idx, used, total))
    return rows


def _linear_leak_rate_mb_per_hour(samples: list[tuple[float, float]]) -> float | None:
    """Least-squares slope of used_mb vs seconds → MB/hour.

    Returns None if we have fewer than 2 samples or the regression is
    degenerate (e.g., all timestamps identical).
    """
    if len(samples) < 2:
        return None
    n = len(samples)
    # Normalize time to seconds-from-start for numerical stability.
    t0 = samples[0][0]
    ts = [s[0] - t0 for s in samples]
    ys = [s[1] for s in samples]
    mean_t = sum(ts) / n
    mean_y = sum(ys) / n
    num = sum((t - mean_t) * (y - mean_y) for t, y in zip(ts, ys))
    den = sum((t - mean_t) ** 2 for t in ts)
    if den == 0:
        return None
    slope_mb_per_sec = num / den
    return slope_mb_per_sec * 3600.0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--duration", type=int, default=60, help="total seconds to sample")
    p.add_argument("--interval", type=float, default=5.0, help="seconds between samples")
    p.add_argument("--out", type=Path, default=Path("./vram_samples.csv"))
    p.add_argument(
        "--device",
        type=int,
        default=0,
        help="GPU index to report leak rate for (CSV keeps all)",
    )
    args = p.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    start = time.time()
    deadline = start + args.duration
    target_samples: list[tuple[float, float]] = []  # (ts, used_mb) for --device

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["unix_ts", "device_index", "used_mb", "total_mb"])
        while time.time() < deadline:
            ts = time.time()
            rows = _query_nvidia_smi()
            if not rows:
                w.writerow([ts, -1, 0, 0])
            else:
                for idx, used, total in rows:
                    w.writerow([ts, idx, used, total])
                    if idx == args.device:
                        target_samples.append((ts, used))
            f.flush()
            # Sleep up to interval, but not past the deadline.
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(args.interval, remaining))

    elapsed = time.time() - start
    print(f"[vram] wrote {args.out}  ({elapsed:.1f}s)")
    if not target_samples:
        print("[vram] no GPU samples captured — skip leak analysis.")
        return 0
    rate = _linear_leak_rate_mb_per_hour(target_samples)
    if rate is None:
        print("[vram] too few samples for leak analysis.")
    else:
        print(f"[vram] device {args.device} leak rate: {rate:+.1f} MB/h")
        gate = 200.0
        status = "PASS" if rate < gate else "FAIL"
        print(f"[vram] V5 gate (<{gate:.0f} MB/h): {status}")
        return 0 if status == "PASS" else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
