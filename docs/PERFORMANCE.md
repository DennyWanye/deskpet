# Performance Baseline (W5 / V5 §1.1)

Three scripts under `scripts/perf/` measure the V5 acceptance gates:

| V5 §1.1 gate | Target | Script |
|---|---|---|
| 第一句响应 | p95 < 2500 ms | `ttft_voice.py` |
| 显存泄漏 | < 200 MB/h | `vram_sampler.py` |
| 常驻稳定性 | 8h+ without crash, <1% error rate | `stability_smoke.py` + `vram_sampler.py` |

All three exit non-zero when a gate fails, so they can be wired into CI
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

## What these do NOT do

- They don't exercise the full voice → tool → memory loop in one run.
  `tests/test_e2e_pipeline.py` is the manual smoke for that.
- They don't measure startup cold-boot time (V5 target <30s) — that's
  still a manual stopwatch exercise against `python main.py` → ready log.
- They don't cover frontend memory (V5 target <60MB RSS for Tauri). For
  that, open Task Manager and watch `deskpet.exe` over a packaged run.
