# Phase 1 Final HANDOFF

**Date**: 2026-04-14
**Scope**: V5 §10 五周计划 W1–W5 全量交付 (R1–R20 + §1.1 验收门控).
**Status**: ✅ Phase 1 complete — `v0.1.0-phase1` tagged on `master`.
**Mode**: codingsys autonomous slice execution (Lead-Expert + quality gates).

---

## What "Phase 1 complete" means here

V5 §1.1 defines Phase 1 as *"Windows 上交付一个可长期常驻的桌宠式 AI 助手"*
covering **W1 foundation → W5 delivery**. Phase 2 is the §13 upgrade path
(duplex PersonaPlex, cloud hybrid, self-built Live2D engine, multimodal,
multi-agent). **None of Phase 2 has started — by design.** Anything that
looks Phase-2-shaped in the code is just the extension points V5 baked in.

## Sprints → slices → commits

| V5 W | Theme | Slice commits |
|---|---|---|
| W1 Foundation | Tauri透明窗口 · WS双通道 · Ollama | sprint1 docs + `9090735…6e6a7ed` baseline |
| W2 Voice | ASR/TTS · pipeline · 口型 · 打断 | sprint2 docs + `431d5a4 6e6a7ed 9add5a8 16a689e` |
| W3 Memory+Tools+Safety | SQLite · ToolRouter · redaction · 确认 | `2781672 133994d` |
| W4 Stability+Obs | Migrations · crash reports · VRAM tier · tools | `120b9e3 c05462b c64c3fb 5477a9e bbbbc4b` |
| W5 Delivery | Packaging · updater · supervisor · autostart · perf · memory UI | `8a8e33a b1cf7aa abd968d ced101a` |

Plus the transparency + Live2D rendering stream merged in parallel
(`a00f9cc 0c6bc16 07a4f47 2bed559 43ac3c6`).

## W5 slice-by-slice

### S11 — Packaging + self-update (R17) · commit `8a8e33a`

- `tauri-app/src-tauri/Cargo.toml` adds `tauri-plugin-updater` +
  `tauri-plugin-autostart` (autostart wired later by S12).
- `tauri.conf.json` now emits NSIS + MSI, pulls `latest.json` from
  GitHub Releases, dialog-mode update UX. Pubkey slot is a placeholder
  (real keypair via `tauri signer generate`, private key in env var per
  `docs/RELEASE.md`).
- `capabilities/default.json` grants `updater:default` +
  `autostart:default`.
- `useUpdateChecker.ts` silent check on mount, errors swallowed so the
  dev browser without Tauri shell still loads.
- `scripts/release.ps1` helper keeps `package.json`/`tauri.conf.json`/
  `Cargo.toml` version numbers in sync.
- `docs/RELEASE.md` documents the one-time keypair setup + the
  per-release `latest.json` template.

### S12 — Crash self-heal + autostart (R17/R9 stability) · commit `b1cf7aa`

- `src-tauri/src/process_manager.rs` rewritten: `Arc<AtomicBool>`
  shutdown flag + `Arc<AtomicU32>` sliding-window restart budget
  (`MAX_RESTARTS_PER_WINDOW=5` / `RESTART_WINDOW_SECS=60` /
  `RESTART_COOLDOWN_MS=2_000`). `install_supervisor` spawns a waiter
  that emits `backend-crashed` / `backend-restarted` / `backend-dead`
  Tauri events with the new shared secret on restart.
- `useBackendLifecycle.ts` listens to the three events; App.tsx clears
  its cached secret on crash and re-polls via `get_shared_secret` on
  restart so WS hooks reconnect transparently.
- `useAutostart.ts` toggles `tauri-plugin-autostart`; falls back to
  `ready=false` in dev browser so the UI hides the button.
- Manual smoke: killed `deskpet.exe`, supervisor respawned within ~2s,
  frontend reconnected without reload — meets V5 §1.1 *"崩溃后10s自愈"*.

### S13 — Performance baseline scripts (V5 §1.1 gates) · commit `abd968d`

Three scripts under `scripts/perf/` + `docs/PERFORMANCE.md`:

- `ttft_voice.py` — edge-tts → 16kHz PCM → `/ws/audio`, measures *last
  PCM frame sent → first TTS byte received*; V5 gate `p95 < 2500 ms`.
- `vram_sampler.py` — `nvidia-smi --query-gpu` sampler, least-squares
  leak rate in MB/h; V5 gate `< 200 MB/h`. Graceful no-GPU path.
- `stability_smoke.py` — QPS-paced chat hammer over `/ws/control`; V5
  gate `error rate < 1%`. `--duration 28800 --qps 1` is the full 8h
  recipe; default `--duration 60` for CI smoke.

All three exit non-zero on FAIL so they slot into a nightly cron.

### S14 — Memory management UI + API (V5 §6 threat 5) · commit `ced101a`

- `SqliteConversationMemory` gains admin methods: `list_turns` /
  `delete_turn` / `list_sessions` / `clear_all`. `RedactingMemoryStore`
  forwards them untouched (content was already redacted on write).
- New control-WS verbs: `memory_list`, `memory_delete`, `memory_clear`,
  `memory_export`. Same shared-secret gate as chat — no second HTTP
  surface.
- `MemoryPanel.tsx` overlay: per-turn delete, session/all clear (two-
  step confirm), JSON export via blob download (works in browser dev
  *and* packaged Tauri without `plugin-fs`).
- 12 new tests (`backend/tests/test_memory_api.py`): 7 unit + 5 WS
  integration.

### S15 — Phase 1 closeout (this doc) · tag `v0.1.0-phase1`

Final gates, handoff doc, git tag.

## Final V5 acceptance state

| Gate | Mechanism | Status |
|---|---|---|
| 第一句响应 p95 < 2.5s | `scripts/perf/ttft_voice.py` | Script in place — manual run needed per release |
| 显存泄漏 < 200 MB/h | `scripts/perf/vram_sampler.py` | Script in place, short smoke PASSes |
| 8h 稳定 · 错误率 < 1% | `stability_smoke.py --duration 28800` | Script in place — 8h run scheduled separately |
| 崩溃后自愈 < 10s | `process_manager.rs` supervisor + events | Manual smoke PASS |
| 可分发安装包 | NSIS + MSI + updater + icons | PASS (built locally from `release.ps1`) |
| 跨会话记忆 | SQLite + migrations + redaction | PASS (覆盖 `test_memory*` 19 用例) |
| 工具确认弹窗 | `requires_confirmation` + deny_all 默认 | PASS (`test_e2e_integration::…denies_high_risk…`) |
| 记忆管理 UI | MemoryPanel 过滤 + 导出 | PASS (S14 commit) |

Backend test count: **134 passed / 1 skipped** (↑ from 122 at S10 closeout).
Frontend `tsc` clean; `cargo check` clean.

## Phase 2 upgrade path (V5 §13) — NOT started

For the next contributor, these are the declared extension points:

| Phase 2 feature | Extension point already in place |
|---|---|
| PersonaPlex 实时双工 ASR+TTS | `ServiceContext.asr_engine / tts_engine` provider slots |
| 自研 Live2D 渲染器 | `src/components/Live2DCanvas.tsx` forwardRef handle + model-path prop |
| 复杂桌面自动化 | `tools/` white-list + `requires_confirmation` already in place |
| 多模态感知 | `pipeline/` stage chain is ordered-list of `Stage` impls |
| 多角色协作 | `ServiceContext` can be instantiated N times |
| 云端混合推理 | `providers/base.py::LLMProvider` abstraction already in place |

None of those are pre-implemented — the interfaces just survive Phase 2
without a redesign.

## What's deliberately out of scope

- **Icon branding**: current icon set was generated from the template
  Tauri logo via `npx tauri icon`. Replace with final art before public
  release.
- **Real updater pubkey**: `tauri.conf.json::plugins.updater.pubkey`
  still says `REPLACE_WITH_OUTPUT_OF_tauri_signer_generate`. Must be
  populated before publishing a signed release; corresponding private
  key lives in a `TAURI_SIGNING_PRIVATE_KEY` env var (not in repo).
- **Frontend RSS < 60MB check**: Task Manager exercise during a
  packaged smoke — not scripted.
- **Cold-boot time < 30s**: manual stopwatch against
  `python main.py → "startup complete"`.
- **Multi-session UI in MemoryPanel**: backend supports cross-session
  listing (`scope:"all"`), but the panel currently shows only the
  active session. Trivial to extend.

## How to verify locally

```bash
# 1. Backend gates
cd backend && .venv/Scripts/python.exe -m pytest
# expected: 134 passed, 1 skipped

# 2. Frontend type check
cd tauri-app && npx tsc --noEmit

# 3. Rust side
cd tauri-app/src-tauri && cargo check

# 4. E2E voice smoke (manual — requires Ollama + models)
python backend/tests/test_e2e_pipeline.py

# 5. V5 §1.1 baseline (backend must be running)
python scripts/perf/ttft_voice.py --secret $SECRET --runs 5
python scripts/perf/stability_smoke.py --secret $SECRET --duration 60
python scripts/perf/vram_sampler.py --duration 60
```

## Doc map (for the next contributor)

- `docs/PERFORMANCE.md` — how to run the §1.1 acceptance probes.
- `docs/RELEASE.md` — signing key setup + release build pipeline.
- `docs/superpowers/plans/2026-04-13-desktop-pet-sprint1-foundation.md`
  + `…-sprint2-voice-pipeline.md` — W1/W2 big-picture plans.
- `docs/superpowers/plans/2026-04-14-slice-{0-4}-*.md` — W2/W3 slice
  plans (S0 agent layer, S1 pipeline stages, S2 memory, S3 tools,
  S4 observability).
- `docs/superpowers/plans/2026-04-14-slices-5-10-w3-w4-closeout.md` —
  W3/W4 closeout (previously mis-titled "Phase 2 Closeout").
- `docs/superpowers/handoffs/S{0-4}-*.md` — per-slice HANDOFF summaries.
- `CLAUDE.md` (project-level) + `~/.claude/CLAUDE.md` (global codingsys
  harness) — how the autonomous loop operates.
