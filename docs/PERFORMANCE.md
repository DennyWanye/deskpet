# Performance Baseline (W5 / V5 §1.1)

Five scripts under `scripts/perf/` measure the V5 acceptance gates:

| V5 §1.1 gate | Target | Script |
|---|---|---|
| 第一句响应 | p95 < 2500 ms | `ttft_voice.py` |
| 显存泄漏 | < 200 MB/h | `vram_sampler.py` |
| 常驻稳定性 | 8h+ without crash, <1% error rate | `stability_smoke.py` + `vram_sampler.py` |
| 冷启动 | p95 < 30 s | `cold_boot.py` |
| 前端 RSS | < 60 MB (Tauri) / < 200 MB (backend) | `rss_sampler.py` |

All exit non-zero when a gate fails, so they can be wired into CI
or a nightly run.

## Prerequisites

- Backend must be running on `127.0.0.1:8100` (`python backend/main.py`).
- Grab the `SHARED_SECRET=...` line printed on startup — all scripts
  take it via `--secret`.
- For TTFT: edge-tts works offline after the first run; first run needs
  internet to fetch the voice. soundfile + torch/torchaudio come from
  the backend venv already.

## TTFT (voice pipeline)

Measures **last PCM frame sent → first TTS byte received**.

```bash
python scripts/perf/ttft_voice.py --secret $SECRET --runs 5
```

Output ends with a `V5 gate (p95 < 2500 ms): PASS` / `FAIL` line and
the script exit code reflects that.

## VRAM leak rate

Samples `nvidia-smi` every `--interval` seconds for `--duration`
seconds, writes a CSV, and reports a least-squares leak rate in MB/h.

```bash
# 30-minute snapshot, 10s resolution:
python scripts/perf/vram_sampler.py --duration 1800 --interval 10 --out vram.csv

# No-GPU fallback: script still runs, emits used_mb=0 rows and skips the
# leak analysis (exit 0).
```

## Stability smoke

Hammers the control WS with text-chat requests at `--qps` rate for
`--duration` seconds.

```bash
# 1-minute CI smoke:
python scripts/perf/stability_smoke.py --secret $SECRET --duration 60 --qps 2

# Full 8h acceptance run — pair with the VRAM sampler:
python scripts/perf/vram_sampler.py --duration 28800 --interval 60 --out vram_8h.csv &
python scripts/perf/stability_smoke.py --secret $SECRET --duration 28800 --qps 1
```

The 8h gate = error rate < 1% AND VRAM slope < 200 MB/h. Both scripts
exit 0 on pass / 1 on fail — shell-combine with `&& echo ALL PASS`.

## Cold boot

Spawns the backend N times, measures wall-clock seconds from process
start until EITHER `SHARED_SECRET=<hex>` appears on stdout OR
`GET /health` returns 200 — whichever wins. Reports p50 / p95 / max
and fails when p95 exceeds `--gate-sec` (default 30s, matches V5).

```bash
# 3-run smoke against the backend venv on Windows:
backend/.venv/Scripts/python.exe scripts/perf/cold_boot.py \
    --cmd "backend/.venv/Scripts/python.exe backend/main.py" --runs 3

# Stricter gate + CSV output:
python scripts/perf/cold_boot.py --runs 10 --gate-sec 20 --out cold_boot.csv
```

The `--cmd` default (`python backend/main.py`) assumes the current
interpreter has the backend deps installed — usually you want to point
it at the venv python explicitly.

## Process RSS

Samples a running process's RSS every `--interval` seconds via
`psutil`, writes a CSV, and reports a least-squares growth rate in
MB/h. Two gates: a peak RSS cap (`--gate-mb`) and a growth-rate cap
(`--gate-mb-per-hour`, default 50 MB/h — anything more across a ~8h
run is a leak).

```bash
# 30s smoke against the running Tauri frontend (60 MB peak gate):
python scripts/perf/rss_sampler.py --name deskpet.exe --duration 30 --gate-mb 60

# 8h stability run against the Python backend, 1-minute resolution:
python scripts/perf/rss_sampler.py --name-contains uvicorn \
    --duration 28800 --interval 60 --gate-mb 200 --out rss_backend_8h.csv
```

Multiple matching processes are summed — so worker splits can't hide
a leak. `--pid` picks one process exactly; `--name-contains` matches
against name + cmdline (good for Python subprocesses whose exe is just
`python.exe`).

## What these do NOT do

- They don't exercise the full voice → tool → memory loop in one run.
  `tests/test_e2e_pipeline.py` is the manual smoke for that.
