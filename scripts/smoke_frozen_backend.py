"""P3-S4 — Smoke test the frozen backend exe.

Spawns `backend/dist/deskpet-backend/deskpet-backend.exe`, captures
its SHARED_SECRET line from stdout, hits /health, and exits 0 iff
`status == "ok"`.

The frozen exe doesn't yet know where to find models (P3-S6 is what
bundles them). We point it at the dev repo's `backend/models/` via
`DESKPET_MODEL_ROOT` so this test exercises the real faster-whisper
/ silero-vad / edge-tts load path — the whole point is to catch
missing hidden imports or bad `datas` entries.

Usage (from repo root):
    python scripts/smoke_frozen_backend.py
Exit codes:
    0 — SHARED_SECRET printed + /health == "ok"
    1 — any failure (timeout / exit / degraded / HTTP error)
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
EXE = REPO_ROOT / "backend" / "dist" / "deskpet-backend" / "deskpet-backend.exe"
MODEL_ROOT = REPO_ROOT / "backend" / "models"

BOOT_TIMEOUT_SEC = 120   # fat stack (torch + cuda init) — generous
HEALTH_TIMEOUT_SEC = 10


def fatal(msg: str) -> None:
    print(f"[smoke] FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    if not EXE.exists():
        fatal(f"{EXE} not built — run `powershell scripts/build_backend.ps1` first")
    if not MODEL_ROOT.exists():
        fatal(
            f"{MODEL_ROOT} not found. P3-S6 will bundle models; until then "
            "the dev repo's backend/models/ must exist for the smoke test."
        )

    env = {**os.environ, "DESKPET_MODEL_ROOT": str(MODEL_ROOT)}
    print(f"[smoke] spawning {EXE}")
    print(f"[smoke] DESKPET_MODEL_ROOT={MODEL_ROOT}")

    t0 = time.time()
    proc = subprocess.Popen(
        [str(EXE)],
        cwd=EXE.parent,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    secret: str | None = None
    try:
        assert proc.stdout is not None
        while True:
            if time.time() - t0 > BOOT_TIMEOUT_SEC:
                proc.kill()
                fatal(f"timeout waiting for SHARED_SECRET (>{BOOT_TIMEOUT_SEC}s)")

            line = proc.stdout.readline()
            if not line:
                proc.wait(timeout=5)
                fatal(f"process exited rc={proc.returncode} before SHARED_SECRET")

            print(f"[backend] {line}", end="")
            if line.startswith("SHARED_SECRET="):
                secret = line.split("=", 1)[1].strip()
                break

        boot_time = time.time() - t0
        print(f"\n[smoke] boot time: {boot_time:.1f}s")
        print(f"[smoke] SHARED_SECRET={secret[:8]}... (len={len(secret)})")

        # Keep draining stdout in a thread so the pipe doesn't block.
        import threading
        def _drain() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                print(f"[backend] {line}", end="")
        threading.Thread(target=_drain, daemon=True).start()

        # /health probe
        health_url = f"http://127.0.0.1:8100/health"
        for attempt in range(10):
            try:
                with urllib.request.urlopen(health_url, timeout=HEALTH_TIMEOUT_SEC) as resp:
                    body = json.loads(resp.read())
                break
            except (urllib.error.URLError, ConnectionRefusedError) as e:
                if attempt == 9:
                    fatal(f"/health unreachable after 10 tries: {e}")
                time.sleep(0.5)

        print(f"[smoke] /health: {json.dumps(body, ensure_ascii=False)}")
        if body.get("status") != "ok":
            fatal(f"degraded status: {body}")
        if body.get("startup_errors"):
            fatal(f"startup_errors non-empty: {body['startup_errors']}")

        print(f"\n[smoke] PASS (boot {boot_time:.1f}s)")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    main()
