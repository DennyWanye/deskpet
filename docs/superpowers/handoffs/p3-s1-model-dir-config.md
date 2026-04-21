# P3-S1 模型目录收拢 + Config 分离 — HANDOFF

**Slice:** Phase 3 Sprint 1 — model dir cleanup + config separation
**Branch:** `worktree-p3-s1-model-dir-config` → pending merge to `master`
**Status:** Code + pytest DONE. **Manual verification PENDING**
**Plan:** `docs/superpowers/plans/2026-04-21-p3s1-model-dir-config.md`
**Parent roadmap:** `docs/superpowers/plans/2026-04-21-phase3-roadmap.md`

## Goal recap

Phase 3 needs to PyInstaller-freeze the backend, but model paths were
hardcoded in `backend/main.py` (`Path(__file__).parent / "assets" / ...`).
Under `--onedir` the layout changes and those paths break silently.

This slice centralises path resolution into **one function** so every
later P3 slice (supervisor rewrite, spec file, model bundling) can build
on a stable foundation.

## Commits

| # | SHA | Title |
|---|-----|-------|
| 1 | `f3a17fa` | test(P3-S1): paths.model_root + resolve_model_dir 单测 |
| 2 | `046eb70` | feat(P3-S1): backend/paths.py 模型路径解析单点 |
| 3 | `978ace3` | test(P3-S1): ASRConfig/TTSConfig.model_dir + 迁移测试 |
| 4 | `2031e0a` | feat(P3-S1): config model_dir 字段 + 兼容迁移 |
| 5 | `f500204` | refactor(P3-S1): main.py 改用 resolve_model_dir |
| 6 | `2b18fe6` | chore(P3-S1): config.toml + .gitignore assets→models |
| 7 | `383641b` | chore(P3-S1): scripts/check_no_hardcoded_assets.py CI 守门 |
| 8 | (this commit) | docs(P3-S1): PACKAGING.md + handoff + STATE 更新 |

## What changed

### New: `backend/paths.py`

Three-tier resolver: `DESKPET_MODEL_ROOT` env → `sys._MEIPASS/models` →
`backend/models/` beside `paths.py`. Plus `resolve_model_dir(subdir)`
that joins + resolves to absolute Path without existence check.

### `backend/config.py`

- `ASRConfig.model_dir = "faster-whisper-large-v3-turbo"` (new field)
- `TTSConfig.model_dir` default changed `"./assets/cosyvoice2"` →
  `"cosyvoice2"`
- `load_config` now detects legacy prefixes (`./assets/`, `assets/`, `./`)
  on `[tts].model_dir`, auto-strips them, and logs a WARNING. Existing
  installs keep running; users see a nudge in the logs.

### `backend/main.py`

Both `Path(__file__).parent / "assets" / ...` call sites replaced with
`resolve_model_dir(config.xxx.model_dir)`. New import:
`from paths import resolve_model_dir`.

### `config.toml`

- `[asr]` gets a new `model_dir = "faster-whisper-large-v3-turbo"` line
  with a comment explaining the dev-mode vs PyInstaller resolution
- `[tts].model_dir` changed from `"./assets/cosyvoice2"` to `"cosyvoice2"`

### `.gitignore`

Added `backend/models/` alongside the existing `backend/assets/`. Keeping
both lets mid-migration devs keep their old weights dir until they rename.

### `scripts/check_no_hardcoded_assets.py`

CI guard: scans `backend/*.py` (excluding tests) for quoted `"assets/..."`
literals and `Path / "assets"` segments. Supports per-line
`p3-s1-allow-assets` escape-hatch comment for the legitimate legacy-
migration tuple in `config.py`.

### `docs/PACKAGING.md`

New skeleton. 5 sections: overview, path conventions, dev mode,
PyInstaller mode (placeholder for P3-S4), troubleshooting.

## Test results

```
280 passed, 4 skipped in ~10s
```

Baseline was 267 (end of P2-2-F1). Added:

- `backend/tests/test_paths.py` — 6 tests (env override, MEIPASS, dev
  mode, resolve_model_dir join, non-existence tolerance, env beats MEIPASS)
- `backend/tests/test_config.py` — +4 tests (TTS model_dir default,
  legacy `./assets/` strip, bare `assets/` strip, non-legacy untouched)
- `backend/tests/test_config_asr.py` — +2 tests (ASR model_dir default,
  custom value load)

Total: 13 new tests, 0 regressions.

`scripts/check_no_hardcoded_assets.py` exit 0 on backend/.

## Manual verification — PENDING

**Required before merging to master**:

1. Rename local `backend/assets/` → `backend/models/` on the dev machine
   (or keep both; config points to `models/`)
2. Start backend: `cd backend && uv run python main.py`
3. Confirm log shows:
   ```
   loading faster-whisper model ... G:\...\backend\models\faster-whisper-large-v3-turbo
   ```
   (not `assets/`)
4. Smoke test one ASR + one TTS round trip; no regression vs P2-2-F1
5. Set `DESKPET_MODEL_ROOT=X:\bogus` → expect backend to fail loading
   from `X:\bogus\faster-whisper-large-v3-turbo` (proves env override works)

## Design decisions

### Why `sys._MEIPASS / "models"` instead of `sys.executable.parent / "models"`?

PyInstaller `--onedir` sets `_MEIPASS` to the bundle's `_internal`
directory; `--onefile` sets it to the temp extract dir. Both point at
the place where `datas=` entries land. Using `sys.executable` would work
for `--onedir` but break `--onefile`. `_MEIPASS` covers both uniformly.

### Why keep both `backend/assets/` AND `backend/models/` in .gitignore?

Lets a dev who pulls this branch keep their existing weights folder
working (legacy path still recognised by config migration) while new
clones grab models into the canonical location. Zero forced churn.

### Why an inline `p3-s1-allow-assets` escape hatch instead of a config file?

Only one real legitimate usage (legacy migration tuple in
`config.py`). Adding a YAML / JSON allow-list for a single entry is
over-engineered; the inline marker is self-documenting and grep-able.

### Why no CPU fallback path?

P3 roadmap decision (signed off 2026-04-21): DeskPet v0.3.x is
NVIDIA-only. CPU fallback would require shipping int8 models, testing a
second code path, and absorbing a 5-10× latency hit. The launcher will
show a clear error on unsupported hardware (P3-S2).

## Pending / next steps

### Local dev machine rename

Before running `uv run python main.py` on the main tree, developer
must:

```powershell
cd G:\projects\deskpet\backend
mv assets models  # or: Rename-Item assets models
```

Legacy `[tts].model_dir = "./assets/cosyvoice2"` still works (warned,
stripped). But the physical files need to live at `backend/models/`
now, not `backend/assets/`.

### Next slice: P3-S2 — CUDA detection + "unsupported hardware" launcher error

Uses `resolve_model_dir` to know where to even look for models, so this
slice was a prerequisite. See
`docs/superpowers/plans/2026-04-21-phase3-roadmap.md` §4.

## Known limitations

1. **No auto-migration for `backend/assets/` → `backend/models/` on the
   filesystem**. Config-side migration handles the TOML, but the actual
   weight files need `mv`. Documented in handoff + PACKAGING.md; too
   risky to do automatically (what if the user already has stuff in
   `models/`?).
2. **silero-vad doesn't go through `resolve_model_dir`**. It's a pip
   package's bundled torch.hub cache. P3-S4 spec file will handle this
   separately.
3. **`scripts/check_no_hardcoded_assets.py` uses regex, not AST**.
   Catches the common shapes but won't catch e.g. `"asse" + "ts/foo"`
   obfuscation. Acceptable — this is a nudge, not a security boundary.
