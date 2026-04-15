"""Measure backend cold-boot time — replaces the "manual stopwatch
against python main.py" footnote in docs/PERFORMANCE.md.

Feeds the V5 §1.1 "< 30s cold-boot" gate. Each run:

1. Spawns `python backend/main.py` as a fresh subprocess (so any in-
   memory caches from a previous run can't skew the measurement).
2. Starts a wall clock the instant the process is spawned.
3. Considers the backend "ready" when EITHER
     - the child prints a line matching `SHARED_SECRET=<hex>` on stdout
       (the very last thing main.py prints before uvicorn serves), OR
     - `GET http://127.0.0.1:<port>/health` returns 200 with
       `{"status":"ok"}`.
   We race both so we don't depend on log-buffering quirks on Windows.
4. Kills the backend and records the elapsed seconds.
5. Repeats --runs times and reports p50 / p95 / max.

Exit code:
    0 = p95 <= --gate-sec (default 30.0, matches V5) OR --runs < 2
    1 = p95 exceeded gate
    2 = configuration error / backend failed to boot even once

Usage:
    # 3-run smoke (default):
    python scripts/perf/cold_boot.py

    # 10 runs, stricter 20s gate, custom CSV:
    python scripts/perf/cold_boot.py --runs 10 --gate-sec 20 --out cold_boot.csv

    # Against a non-default port / custom backend entrypoint:
    python scripts/perf/cold_boot.py --port 8101 \\
        --cmd "python backend/main.py --port 8101"
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

SECRET_RE = re.compile(r"SHARED_SECRET=([0-9a-fA-F]+)")


def _tail_stdout_for_secret(
    proc: subprocess.Popen[str], ready_evt: threading.Event, sink: list[str]
) -> None:
    """Drain child stdout; flip ready_evt the instant we see SHARED_SECRET=.

    We keep draining after ready fires — otherwise the pipe's OS buffer
    can fill and the child blocks on its next print, inflating later
    cold-boot measurements in the same session.
    """
    assert proc.stdout is not None
    for line in proc.stdout:
        sink.append(line)
        if not ready_evt.is_set() and SECRET_RE.search(line):
            ready_evt.set()


def _poll_health(port: int, ready_evt: threading.Event, stop_evt: threading.Event) -> None:
    """Hit /health every 100ms until either event fires."""
    url = f"http://127.0.0.1:{port}/health"
    while not ready_evt.is_set() and not stop_evt.is_set():
        try:
            with urllib.request.urlopen(url, timeout=0.5) as r:
                if r.status == 200:
                    ready_evt.set()
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            pass
        time.sleep(0.1)


def _one_run(cmd: list[str], port: int, timeout_sec: float) -> tuple[float, str]:
    """Boot the backend once, return (elapsed_sec, reason).

    reason ∈ {"secret_line", "health_ok", "timeout", "crashed"}.
    Always tears down the child before returning.
    """
    ready_evt = threading.Event()
    stop_evt = threading.Event()
    stdout_sink: list[str] = []

    # Start the subprocess capturing stdout line-by-line. stderr is
    # merged in so tracebacks don't get lost.
    t0 = time.perf_counter()
    # On Windows, CREATE_NEW_PROCESS_GROUP lets us send CTRL_BREAK; on
    # POSIX we just rely on terminate/kill.
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,  # line-buffered
        creationflags=creationflags,
    )

    # Start the two watchers in parallel.
    t_stdout = threading.Thread(
        target=_tail_stdout_for_secret,
        args=(proc, ready_evt, stdout_sink),
        daemon=True,
    )
    t_health = threading.Thread(
        target=_poll_health, args=(port, ready_evt, stop_evt), daemon=True
    )
    t_stdout.start()
    t_health.start()

    # Wait for ready or timeout. We poll once per 50ms so both the
    # deadline and an early crash are caught quickly.
    deadline = t0 + timeout_sec
    reason = "timeout"
    while time.perf_counter() < deadline:
        if ready_evt.is_set():
            # Decide which watcher won. If the stdout tail already
            # observed the secret line, prefer that; otherwise the
            # health poll got there first.
            if any(SECRET_RE.search(s) for s in stdout_sink):
                reason = "secret_line"
            else:
                reason = "health_ok"
            break
        if proc.poll() is not None:
            reason = "crashed"
            break
        time.sleep(0.05)
    elapsed = time.perf_counter() - t0

    # Tear down. terminate → wait → kill, the usual dance.
    stop_evt.set()
    if proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    # Flush the stdout tail so the next run starts from a clean slate.
    t_stdout.join(timeout=2)
    t_health.join(timeout=2)

    if reason == "crashed":
        tail = "".join(stdout_sink[-20:])
        sys.stderr.write(
            "[cold] backend exited before ready. Last stdout lines:\n"
            f"{tail}\n"
        )

    return elapsed, reason


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile — same convention as ttft_voice.py."""
    if not values:
        return float("nan")
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(pct / 100.0 * (len(s) - 1)))))
    return s[k]


def main() -> int:
    p = argparse.ArgumentParser(description="Measure backend cold-boot time.")
    p.add_argument(
        "--cmd",
        default="python backend/main.py",
        help="command to launch the backend (default: python backend/main.py)",
    )
    p.add_argument(
        "--port",
        type=int,
        default=8100,
        help="port to probe /health on (default: 8100 — must match --cmd)",
    )
    p.add_argument("--runs", type=int, default=3, help="how many cold boots to time")
    p.add_argument(
        "--timeout-sec",
        type=float,
        default=60.0,
        help="per-run timeout; treated as a FAIL (default 60s, 2x the V5 gate)",
    )
    p.add_argument(
        "--gate-sec",
        type=float,
        default=30.0,
        help="fail if p95 exceeds this many seconds (V5 §1.1: 30s)",
    )
    p.add_argument(
        "--cooldown-sec",
        type=float,
        default=2.0,
        help="sleep between runs so ports / file handles fully release",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=Path("./cold_boot_samples.csv"),
        help="CSV of (run_index, elapsed_sec, reason)",
    )
    args = p.parse_args()

    if args.runs < 1:
        sys.stderr.write("[cold] --runs must be >= 1\n")
        return 2

    cmd = shlex.split(args.cmd, posix=(os.name != "nt"))
    # Windows Popen without shell= won't resolve relative paths against CWD
    # for the executable slot — it only searches PATH. So if the first arg
    # looks like a relative filesystem path (has a separator and the file
    # exists), absolutize it. Keeps `python backend/main.py` working on
    # Linux/macOS where relative resolution is fine, and lets Windows
    # users pass `backend/.venv/Scripts/python.exe backend/main.py` too.
    if cmd:
        exe = Path(cmd[0])
        if not exe.is_absolute() and ("/" in cmd[0] or "\\" in cmd[0]):
            resolved = exe.resolve()
            if resolved.exists():
                cmd[0] = str(resolved)
    print(f"[cold] cmd: {cmd}")
    print(f"[cold] port: {args.port}  runs: {args.runs}  timeout: {args.timeout_sec}s")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    elapsed_all: list[float] = []
    any_success = False

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["run_index", "elapsed_sec", "reason"])
        for i in range(args.runs):
            elapsed, reason = _one_run(cmd, args.port, args.timeout_sec)
            w.writerow([i, f"{elapsed:.3f}", reason])
            f.flush()
            ok = reason in ("secret_line", "health_ok")
            marker = "OK" if ok else "FAIL"
            print(f"[cold]   run {i+1}/{args.runs}: {elapsed:6.2f}s  {reason:12s}  {marker}")
            if ok:
                elapsed_all.append(elapsed)
                any_success = True
            # Cooldown between runs — only if another run is coming.
            if i + 1 < args.runs:
                time.sleep(args.cooldown_sec)

    print(f"[cold] wrote {args.out}  ({len(elapsed_all)}/{args.runs} succeeded)")

    if not any_success:
        print("[cold] FAIL: backend never booted successfully")
        return 2

    p50 = _percentile(elapsed_all, 50)
    p95 = _percentile(elapsed_all, 95)
    peak = max(elapsed_all)
    print(f"[cold] p50={p50:.2f}s  p95={p95:.2f}s  max={peak:.2f}s")

    if len(elapsed_all) < 2:
        print("[cold] only one successful run — skipping p95 gate")
        return 0

    gate_status = "PASS" if p95 <= args.gate_sec else "FAIL"
    print(f"[cold] p95 gate (<= {args.gate_sec:.0f}s): {gate_status}")
    return 0 if gate_status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
