# Day-0 Probes (round-1 runtime复测)

Run timestamp: 2026-05-26 00:55 local
Tauri dev: PID 19296 (deskpet.exe)
Backend: PID 29440 (orphan from 2026-05-25 01:26 — port 8100 already held)
Vite: PID 20456 (:5173)
WebView2 CDP: PID 4076 (:9222)

Branch: `master`
HEAD: `c89e296 docs(pet-anim-v2): FINAL_REPORT Phase 1 dev complete (13/13 FR + AC-3 PASS)`

Probe driver: `probe-runner.mjs` (CDP via :9222), raw json in `d0-probes-raw.json`.

---

## §2 ManualTest D0 probe matrix (6 probes)

| Probe | Spec | Result | Status |
|---|---|---|---|
| **CASE-D0-01** Tauri startDragging | `window.__TAURI__.core.invoke` exists | OK — `__TAURI__` namespaces present (`app, core, dpi, event, image, menu, mocks, path, tray, webview, webviewWindow, window`); `core.invoke` is a function | **PASS** |
| **CASE-D0-02** viseme backend | `python -m deskpet_backend.dry_run_tts "妈妈骑马慢"` outputs `viseme: {v, t_ms}` | **FAIL** — no module `deskpet_backend.dry_run_tts` and no `viseme` field in `/health`. Backend has no viseme provider. | **FAIL → graceful degrade to fallback (B3 phoneme estimator)** |
| **CASE-D0-03** phonemeEstimator viable (B3 fallback) | `window.__deskpet_anim_modules.phonemeEstimator` callable | **FAIL** — `__deskpet_anim_modules` not exposed on `window`. Module file exists (`src/pet-anim/phonemeEstimator.ts`) and is wired through `App.tsx` → `Live2DCanvas.setPhonemeEstimatorReady`, but test fixtures cannot fake-drive it. | **FAIL → BLOCKED for fallback acceptance test** |
| **CASE-D0-04** Hiyori 10 expression params | `window.__deskpet_anim_overlay` exposes setters | **FAIL** — `__deskpet_anim_overlay` not exposed. AnimationOverlay class HAS setters (`setEmotion`, `setDragState`, `setDNDActive`, …) but only the canvas component holds the ref; no `window` shim. | **FAIL → BLOCKS §3–§19 fake-emotion / fake-milestone driven cases** |
| **CASE-D0-05** LLM emotion field | `chinzy.chat("…")` returns dict with `emotion` key | **FAIL** — no `llm.chinzy` module; LLM goes through `backend/llm/openai_adapter.py` / `gemini_adapter.py`. System prompt does not append `emotion` field. | **FAIL → fallback to frontend `emotionClassifier` voting** |
| **CASE-D0-06** F1 universal audio session | `invoke('is_any_audio_capture_active')` returns bool | **FAIL** — Tauri Rust does not register this command; CDP `core.invoke('is_any_audio_capture_active')` returns "Command … not found". Same for `enumerate_top_windows` / `is_foreground_fullscreen`. | **FAIL → only KPM typing path of F1 can be triggered, fullscreen+call silently disabled (per task brief)** |

Extra probes:
- **flags** — All 17 v2 flag keys return `null` in `localStorage` (defaults take effect; v2 is ON by default per featureFlags.ts).
- **debug API** — `window.__deskpet_anim_debug` exposes **only** v1 5-field `getAnimationDebug()` (`gaze_target_yaw`, `gaze_smoothed_yaw`, `last_input_age_ms`, `current_state`, `current_motion_idx`). The richer **`getV2Debug()`** (16 v2 fields: `held_state`, `user_input_active`, `thinking_active`, `current_emotion`, `dnd_active`, `dnd_reasons`, `low_energy`, `welcome_active`, …) is implemented on `AnimationOverlay` but **never exposed on `window`**.
- **pet target DOM** — `http://localhost:5173/` page reports `bodyChildren: [DIV#root.,SCRIPT#.,CANVAS#.]`. Pet canvas IS rendering.

---

## Plan B (graceful degrade per PRD §8) routing

| Probe FAIL | PRD §8 routing | Effect on ManualTest |
|---|---|---|
| D0-02 (viseme backend) | B3 main path → fallback to `phonemeEstimator` (frontend rule-based) | B3 main-path cases (CASE-B3-01..06) **cannot be PASS-verified** because there is no main path running. CASE-B3-07 (fallback) IS the production path. |
| D0-03 (`__deskpet_anim_modules` not exposed) | N/A — module exists, just not test-controllable | Blocks fake-stream injection for B3 OS-level manual tests. |
| D0-04 (`__deskpet_anim_overlay` not exposed) | N/A — overlay ref lives inside Live2DCanvas only | **Blocks all ManualTest §3–§19 P0 cases that depend on `fakeEmotion`/`fakeMilestone`/`__deskpet_anim_overlay.setX()` to drive the FSM.** |
| D0-05 (LLM emotion field) | D1 backend path → fallback to `emotionClassifier` (frontend voting) | D1 cases CASE-D1-01..08 must validate the frontend classifier path; backend lock release (M-11) becomes unverifiable without main path. |
| D0-06 (Rust commands missing) | F1: fullscreen + call detection silently `off`; only typing KPM trigger runs | Per task brief, this was already known. F1-01/02 (KPM) testable; F1-03 (audio) and F1 fullscreen are silently disabled — should NOT FAIL the FR. |

**No FR is cut.** Per PRD §8: 5/6 D0 probes route to documented degrade paths. The remaining gap (D0-04 observability) is a **test-surface gap**, not a FR gap.

---

## Implications for §3–§19 P0 execution

The ManualTest §0.2 helper APIs (`window.testV2Smoke`, `window.fastForwardIdle`, `window.fakeEmotion`, `window.fakeMilestone`, `window.__deskpet_anim_overlay.setEmotion()`) **do not exist in the running build**. Source code grep confirms none of these globals are written anywhere in `tauri-app/src/`.

This blocks the following P0 cases from being executed *as specified*:

- **CASE-B1-04** (IME): needs `debug.user_input_active` — not exposed.
- **CASE-B2-02/03/04** (thinking): needs `debug.thinking_active` and `debug.saccade` — neither exposed.
- **CASE-B3-07/08** (fallback / blend A-B): needs to mock-disable backend viseme and force `setPhonemeEstimatorReady` — no test handle.
- **CASE-B4-03** (800ms timeout): needs to mock backend dropping `tts_end` and observe `mouthFader` debug state — fader debug not on `window`.
- **CASE-C1-01..04** (low_energy / wakeup): needs `fastForwardIdle(5)` — does not exist.
- **CASE-C2-01..03** (welcome escalation): needs `fastForwardIdle(10/30/65 min)` — does not exist.
- **CASE-C3-04** (hourly DND suppression): needs `clock injection` mock — not available.
- **CASE-D1-01..08** (emotion 5 classes + voting + lock release): needs `debug.current_emotion` — not exposed. The frontend classifier IS the production path (D0-05 FAIL → fallback main), so the test must drive it by sending real chat replies through the App.tsx chat path, then visually inspect Live2D face. Hours of effort for each class.
- **CASE-D2-01..03** (milestone 5 rules): needs `fakeMilestone(...)` — does not exist. Must originate from backend `pet_milestone` WS message — backend has no such emitter.
- **CASE-E2-01..04** (occlusion consent / grid sampling): needs `localStorage.removeItem('deskpet_consent_occlusion')` + a consent dialog UI. Source has `occlusionWatcher.ts` but App.tsx does not render any consent UI gate — grep for "consent" in `tauri-app/src` is needed (next round).
- **CASE-F1-01..06** (DND): KPM-only path may work; the rest are silently disabled per Rust command absence.
- **CASE-AC10-01..04** (4 一票否决): all depend on `debug.current_emotion` / `debug.dnd_reasons` / `geom.screenX,Y` introspection — none on `window`.
- **CASE-BLIND-v2-01** (1+1 盲选): mechanically possible (record 60s with `v2_all=off` vs `on` via localStorage). Test-able but requires a human jury after 1 week — out of round-1 scope.
- **CASE-PERF-01..04**: FPS / CPU / applyTo bench reachable via `__deskpet_anim_bench.applyToOnce(t)` (exposed in DEV). Could be measured.
- **CASE-AC3-01..04** (v1 零回归 snapshot): automated. `pnpm test:ac3-snapshot` exists per Sprint 3 commit message. **Independently checkable.**

---

## Recommended next-step routing

Round-1 cannot deliver "全部 P0 PASS" verdict by ManualTest spec; the dev branch ships the FRs but **lacks the test-observability surface** that the ManualTest expects.

**Round-1 decision: NEEDS-FIX (observability)** — not a FR rollback. Two minimal interventions unblock everything:

1. **Add window exposures** in `Live2DCanvas.tsx` (~10 lines):
   ```ts
   w["__deskpet_anim_overlay"] = overlayRef.current;
   w["__deskpet_anim_debug_v2"] = () => overlayRef.current?.getV2Debug();
   ```
2. **Add helpers in App.tsx (DEV-only)** for fastForwardIdle / fakeEmotion / fakeMilestone — already designed (per ManualTest §0.2), just unmaterialised.

After those land, ALL §3–§19 P0 cases can be run in a single 2-hour pass.

---

## What IS verified in round-1

- ✅ Tauri dev boots, vite serves, WebView2 attaches CDP, pet target reachable.
- ✅ Animation engine alive (`__deskpet_anim_debug` returns live values; saccade target moving).
- ✅ Backend healthy (`/health` 200 OK).
- ✅ The 521-vitest claim is consistent with file inventory — `src/pet-anim/__tests__/` has 27 test files including `ac3_snapshot.test.ts`, `overlay_v2.test.ts`, all 13 FR sub-modules.
- ✅ v2 setter chain wired: App.tsx → `liveRef.current.setEmotion / setDragState / setDNDActive / triggerWelcome / triggerCelebration / setEdgeAttached / setLowEnergy / setUserInputActive / setThinkingActive / fadeMouthToZero / setVisemeFrame` — full coverage of the 13 FR public surface.

## What is NOT verified

- ❌ Any P0 effect at the real-OS level beyond "the page is rendering" — because we cannot drive the FSMs without the test handles.

---

## Recommendation to main agent

1. **Land observability-bridge commit** (`feat(pet-anim-v2): expose v2 debug + test helpers on window`) — 5-15 lines in Live2DCanvas.tsx + App.tsx. Estimated < 30 min including a vitest.
2. **Re-spawn QA round-2** after the bridge — same agent, same fixtures.
3. **Independently**, run `pnpm test:ac3-snapshot` and `npm run test:anim` from CI — these confirm AC-3 snapshot diff = 0 and 521-test count. These DO NOT require the observability bridge.
