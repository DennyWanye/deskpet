# P2-0-S4 性能脚本化 (cold_boot + rss_sampler) — HANDOFF

**Date**: 2026-04-15
**Sprint**: V6 Phase 2 · Sprint P2-0 · Slice 4
**Status**: ✅ Complete
**Target version**: feeds into `v0.2.0-phase2-beta1`

## What shipped

Two new scripts under `scripts/perf/` that replace the manual bits in
`docs/PERFORMANCE.md`:

- `cold_boot.py` — automates the V5 §1.1 "< 30s cold-boot" gate that
  used to say "open a stopwatch against `python main.py`".
- `rss_sampler.py` — automates the "< 60MB Tauri / < 200MB backend"
  RSS gates that used to say "open Task Manager and squint at
  deskpet.exe".

Both scripts exit non-zero on gate failure, so they're CI-ready.

## Commits (master)

| SHA | Subject |
|---|---|
| `16fedb2` | feat(perf): add cold_boot.py + rss_sampler.py (P2-0-S4) |

## `scripts/perf/cold_boot.py`

- Spawns `python backend/main.py` as a fresh subprocess; races two
  ready signals: (1) a `SHARED_SECRET=<hex>` line on stdout, or (2) a
  200 from `GET /health`. Whichever wins ends the wall-clock.
- Tears the child down between runs; cools down `--cooldown-sec` to let
  ports/handles release.
- Reports p50 / p95 / max + per-run CSV (`run_index, elapsed_sec,
  reason`). Exits 1 if p95 > `--gate-sec` (default 30s).
- Windows quirk handled: when `--cmd` starts with a relative path
  (e.g. `backend/.venv/Scripts/python.exe ...`), resolves it against
  CWD before handing to `Popen` — `_winapi.CreateProcess` otherwise
  only searches `PATH`.

**Smoke** (3 runs against local backend venv):

```
[cold]   run 1/3:   1.73s  secret_line   OK
[cold]   run 2/3:   1.70s  secret_line   OK
[cold]   run 3/3:   1.70s  secret_line   OK
[cold] p50=1.70s  p95=1.73s  max=1.73s
[cold] p95 gate (<= 30s): PASS
```

(Backend had warm disk cache so real cold-from-reboot numbers will be
higher. 30s gate has ~17x headroom today — plenty of room to grow.)

## `scripts/perf/rss_sampler.py`

- psutil-backed. Three selectors (mutually exclusive): `--name` /
  `--name-contains` / `--pid`.
- Refreshes the process list each tick so a restarted target gets
  re-picked-up without dropping samples.
- Sums RSS across all matching pids — so uvicorn worker splits can't
  hide a leak.
- Two gates:
  - `--gate-mb` — fails if peak summed RSS ever exceeds this (default
    unset; use 60 for Tauri, 200 for backend).
  - `--gate-mb-per-hour` — least-squares slope gate (default 50 MB/h).
- Added `psutil>=5.9.0` to `backend/pyproject.toml` with a comment
  pointing at this script.

**Smoke** (5s against `python.exe`):

```
[rss] sampling 2 process(es): pids=[10980, 23920]
[rss] wrote .../rss_smoke.csv  (5.0s, 5 ticks)
[rss] growth rate: -1488.6 MB/h
[rss] growth-rate gate (< 50 MB/h): PASS
```

(Negative growth is GC on short windows — expected. Peak-RSS gate not
invoked because `--gate-mb` wasn't passed.)

## docs/PERFORMANCE.md

- Table header now says "Five scripts", two new rows for cold_boot
  and rss_sampler.
- New "Cold boot" and "Process RSS" sections with copy-pasteable
  commands for both the dev-smoke and full-acceptance shapes.
- "What these do NOT do" pruned — the two automated gates are no
  longer on the exception list; only the "full voice→tool→memory
  loop" caveat remains (still covered by `test_e2e_pipeline.py`).

## Gates

- ✅ cold_boot smoke: 3/3 PASS, p95=1.73s < 30s
- ✅ rss_sampler smoke: 5 ticks, growth-rate gate PASS
- ✅ `python -c "import psutil"` inside backend venv

## Follow-ups

- Neither script is wired into CI yet. Natural next slice: add them to
  the nightly/PR workflow alongside the existing perf scripts. The
  exit codes are already CI-shaped.
- cold_boot's local numbers (1.7s) are disk-cache-warm; to get a real
  "first boot after reboot" number we'd need to clear filesystem cache
  between runs (Windows: `Clear-FileSystemCache`, Linux: drop_caches).
  Deferred — 1.7s vs 30s gate has enough headroom that it's not worth
  the root-privilege complication right now.
- rss_sampler doesn't emit Prometheus metrics yet. If we add a
  long-running perf exporter in Phase 3, fold both samplers into it.

## Spec / plan

- Roadmap entry: `docs/superpowers/plans/2026-04-14-phase2-v6-roadmap.md`
  §3.1 slice P2-0-S4
