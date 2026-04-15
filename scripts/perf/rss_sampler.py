"""Sample a process's RSS over time — replaces the "open Task Manager
and squint at deskpet.exe" footnote in docs/PERFORMANCE.md.

Feeds the V5 §1.1 "< 60 MB RSS, frontend" gate for the Tauri client
and the "< 200 MB RSS, backend" sanity check for the Python server.

Writes CSV rows (unix_ts, pid, rss_mb, vms_mb) to --out and prints a
trailing summary with a least-squares growth rate (MB/h) so slow leaks
are caught without eyeballing.

Target selection:
    --name deskpet.exe           # match by process name (Windows: exe;
                                 #   Linux/macOS: binary basename)
    --pid 1234                   # match a single pid exactly
    --name-contains uvicorn      # substring match on the exe or the
                                 #   cmdline — useful for "the Python
                                 #   serving main.py" without caring
                                 #   about the interpreter's name

When multiple processes match --name / --name-contains, all of them
are sampled and summed into the --target-gate (default: the largest
RSS at the first sample) so that worker splits don't hide a leak.

Usage:
    # 30s smoke against the running frontend:
    python scripts/perf/rss_sampler.py --name deskpet.exe --duration 30

    # 8h stability run against the backend, 1-minute resolution:
    python scripts/perf/rss_sampler.py --name-contains uvicorn \\
        --duration 28800 --interval 60 --out rss_backend_8h.csv

    # Gate against a specific number (overrides the default 60 MB frontend
    # gate). Useful when wiring into CI for the backend budget.
    python scripts/perf/rss_sampler.py --name deskpet.exe \\
        --duration 60 --gate-mb 200 --out rss.csv

Exit code:
    0 = gate passed OR no gate was applicable (too few samples)
    1 = growth rate exceeds --gate-mb-per-hour OR peak RSS exceeds
        --gate-mb (whichever applies).
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path
from typing import Iterable

try:
    import psutil
except ImportError as exc:  # pragma: no cover - install issue
    sys.stderr.write(
        "psutil not installed. Run: pip install psutil (or "
        "reinstall the backend venv — pyproject.toml pins it).\n"
    )
    raise SystemExit(2) from exc


def _matches(proc: psutil.Process, name: str | None, contains: str | None) -> bool:
    """Decide whether this process is one we want to sample."""
    try:
        pname = proc.name()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    if name is not None and pname.lower() == name.lower():
        return True
    if contains is not None:
        hay = pname
        try:
            hay = hay + " " + " ".join(proc.cmdline())
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        if contains.lower() in hay.lower():
            return True
    return False


def _find_targets(
    *, name: str | None, contains: str | None, pid: int | None
) -> list[psutil.Process]:
    """Return psutil.Process handles matching the selector.

    Drops processes that disappear mid-scan or that we can't read —
    sampling is best-effort; we never want the sampler itself to crash
    because a worker exited.
    """
    if pid is not None:
        try:
            return [psutil.Process(pid)]
        except psutil.NoSuchProcess:
            return []
    out: list[psutil.Process] = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            if _matches(p, name, contains):
                out.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


def _sample_rss(procs: Iterable[psutil.Process]) -> list[tuple[int, float, float]]:
    """Return (pid, rss_mb, vms_mb) for each live process; skip dead ones."""
    rows: list[tuple[int, float, float]] = []
    for p in procs:
        try:
            mi = p.memory_info()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        rows.append((p.pid, mi.rss / 1024 / 1024, mi.vms / 1024 / 1024))
    return rows


def _linear_growth_rate_mb_per_hour(
    samples: list[tuple[float, float]],
) -> float | None:
    """Least-squares slope of rss_mb vs seconds → MB/hour.

    Copied from vram_sampler so the two scripts stay self-contained.
    Returns None if fewer than 2 samples or degenerate time axis.
    """
    if len(samples) < 2:
        return None
    n = len(samples)
    t0 = samples[0][0]
    ts = [s[0] - t0 for s in samples]
    ys = [s[1] for s in samples]
    mean_t = sum(ts) / n
    mean_y = sum(ys) / n
    num = sum((t - mean_t) * (y - mean_y) for t, y in zip(ts, ys))
    den = sum((t - mean_t) ** 2 for t in ts)
    if den == 0:
        return None
    return num / den * 3600.0


def main() -> int:
    p = argparse.ArgumentParser(description="Sample process RSS over time.")
    sel = p.add_mutually_exclusive_group(required=True)
    sel.add_argument("--name", help="exact process name, e.g. deskpet.exe")
    sel.add_argument("--name-contains", help="substring against name + cmdline")
    sel.add_argument("--pid", type=int, help="exact pid")
    p.add_argument("--duration", type=int, default=60, help="total seconds")
    p.add_argument("--interval", type=float, default=2.0, help="seconds between samples")
    p.add_argument("--out", type=Path, default=Path("./rss_samples.csv"))
    p.add_argument(
        "--gate-mb",
        type=float,
        default=None,
        help="fail if the summed RSS ever exceeds this many MB "
        "(Tauri frontend: 60; backend: 200 typical)",
    )
    p.add_argument(
        "--gate-mb-per-hour",
        type=float,
        default=50.0,
        help="fail if the linear growth rate exceeds this (default 50 MB/h — "
        "matches V5's expectation that neither process should leak faster "
        "than that across a ~8h run)",
    )
    args = p.parse_args()

    procs = _find_targets(
        name=args.name, contains=args.name_contains, pid=args.pid
    )
    if not procs:
        sel_desc = (
            f"name={args.name!r}"
            if args.name
            else (
                f"name-contains={args.name_contains!r}"
                if args.name_contains
                else f"pid={args.pid}"
            )
        )
        sys.stderr.write(f"[rss] no processes match {sel_desc} — is it running?\n")
        return 2

    pids = sorted(p.pid for p in procs)
    print(f"[rss] sampling {len(procs)} process(es): pids={pids}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    start = time.time()
    deadline = start + args.duration
    summed_samples: list[tuple[float, float]] = []
    peak_summed_mb = 0.0

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["unix_ts", "pid", "rss_mb", "vms_mb"])
        while time.time() < deadline:
            ts = time.time()
            # Refresh the process list each tick so a restarted target
            # (e.g. after a dev-mode crash recovery) gets picked up.
            if args.pid is None:
                procs = _find_targets(
                    name=args.name, contains=args.name_contains, pid=None
                )
            rows = _sample_rss(procs)
            if not rows:
                w.writerow([ts, -1, 0.0, 0.0])
            else:
                total_rss = 0.0
                for pid, rss_mb, vms_mb in rows:
                    w.writerow([ts, pid, f"{rss_mb:.2f}", f"{vms_mb:.2f}"])
                    total_rss += rss_mb
                summed_samples.append((ts, total_rss))
                peak_summed_mb = max(peak_summed_mb, total_rss)
            f.flush()
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(args.interval, remaining))

    elapsed = time.time() - start
    print(f"[rss] wrote {args.out}  ({elapsed:.1f}s, {len(summed_samples)} ticks)")

    failures: list[str] = []

    if args.gate_mb is not None:
        print(f"[rss] peak RSS (summed across pids): {peak_summed_mb:.1f} MB")
        gate_status = "PASS" if peak_summed_mb <= args.gate_mb else "FAIL"
        print(f"[rss] peak-RSS gate (<= {args.gate_mb:.0f} MB): {gate_status}")
        if gate_status == "FAIL":
            failures.append(f"peak {peak_summed_mb:.1f} MB > gate {args.gate_mb:.0f} MB")

    rate = _linear_growth_rate_mb_per_hour(summed_samples)
    if rate is None:
        print("[rss] too few samples for growth-rate analysis.")
    else:
        print(f"[rss] growth rate: {rate:+.1f} MB/h")
        rate_status = "PASS" if rate < args.gate_mb_per_hour else "FAIL"
        print(
            f"[rss] growth-rate gate (< {args.gate_mb_per_hour:.0f} MB/h): {rate_status}"
        )
        if rate_status == "FAIL":
            failures.append(
                f"growth {rate:+.1f} MB/h >= gate {args.gate_mb_per_hour:.0f} MB/h"
            )

    if failures:
        print("[rss] FAIL: " + "; ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
