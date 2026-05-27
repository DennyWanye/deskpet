# FAIL — Observability bridge missing (blocks §3–§19 P0 cases)

| 项 | 值 |
|---|---|
| Case ID | meta-fail (blocks 13+ P0 cases) |
| FR | observability surface, not a FR |
| Severity | **NEEDS-FIX** (round-1 cannot continue without this) |
| Round | round-1 |

## Symptom

`ManualTest.md §0.2` documents these DevTools helpers:

```js
window.testV2Smoke      // 13 FR mini smoke
window.fastForwardIdle  // (minutes) → drives idleWatcher
window.fakeEmotion      // mock setEmotion
window.fakeMilestone    // mock pet_milestone payload
window.__deskpet_anim_overlay.setEmotion(em, performance.now())  // direct setter
```

Runtime check (after pet rendered & 30 FPS confirmed):

```
[D0-04-overlay-params] OK "no-overlay"          ← __deskpet_anim_overlay does not exist
[D0-X-debug-api]      OK keys: [gaze_target_yaw, gaze_smoothed_yaw, last_input_age_ms,
                                  current_state, current_motion_idx]
                                                ← only v1 5-field; getV2Debug() not exposed
```

`grep -rn '__deskpet_anim_overlay\|fakeEmotion\|fakeMilestone\|fastForwardIdle\|testV2Smoke' tauri-app/src/` returns **zero matches**. None of the helpers exist in the codebase.

## Source of truth

`tauri-app/src/components/Live2DCanvas.tsx:432-462` only writes:

```ts
w["__deskpet_anim_metrics"]  = () => overlayRef.current?.getAnimationMetrics();
w["__deskpet_anim_debug"]    = overlayRef.current?.getAnimationDebug();   // v1 5-field
w["__deskpet_anim_bench"]    = { applyToOnce: (t) => … };                 // DEV only
```

`getV2Debug()` is implemented in `AnimationOverlay` (`tauri-app/src/pet-anim/index.ts:410-446`) returning all 16 v2 fields (held_state, user_input_active, thinking_active, mouth_fade_mode, current_emotion, viseme_queue_size, low_energy, welcome_active, welcome_intensity, edge_attached, dnd_active, dnd_reasons, celebration_active, red_alert_active, …). **It is never exposed on `window`.**

The setter chain (`liveRef.current.setEmotion / setDragState / setDNDActive / setLowEnergy / triggerWelcome / triggerCelebration / setEdgeAttached / setUserInputActive / setThinkingActive / fadeMouthToZero / setVisemeFrame`) IS wired in `App.tsx` → `Live2DCanvas.tsx` ref API. It just isn't reachable from DevTools.

## P0 cases blocked

| Case | Blocked by | Workaround |
|---|---|---|
| CASE-B1-04 IME | `debug.user_input_active` | none |
| CASE-B2-02/03/04 thinking | `debug.thinking_active`, `debug.saccade` | none |
| CASE-B3-07/08 fallback / blend A-B | mock setPhonemeEstimatorReady | none |
| CASE-B4-03 800ms timeout | `mouth_fade_mode` debug | none |
| CASE-C1-01..04 low_energy / wakeup | `fastForwardIdle()` | wait 5+ minutes real-time per case (~30 min) |
| CASE-C2-01..03 welcome escalation | `fastForwardIdle(10/30/65)` | hours real-time |
| CASE-C3-04 hourly DND suppress | clock injection | wait real hour |
| CASE-D1-01..08 emotion + voting + lock | `debug.current_emotion`, `fakeEmotion` | visual face inspection only — cannot assert classifier verdict |
| CASE-D2-01..03 milestone 5 rules | `fakeMilestone()` | backend has no `pet_milestone` emitter to drive it from backend side either (separate gap) |
| CASE-E2-01..04 occlusion consent | `localStorage.removeItem('deskpet_consent_occlusion')` + consent UI | check whether App.tsx renders consent UI (not yet inspected) |
| CASE-AC10-01 sad ≠ happy | `debug.current_emotion` | none |
| CASE-AC10-02 not off-screen | `geom.screenX / screenY` | partial — Tauri window.outerPosition reachable |
| CASE-AC10-03 red alert ignores DND | `debug.red_alert_active`, `debug.dnd_active` | none |
| CASE-AC10-04 drag ≠ click | `metrics.interaction.samples` IS exposed via `__deskpet_anim_metrics()` | **TESTABLE** (but synthetic events don't fire — need real OS mouse, see notes) |

## Repro

```bash
# After Tauri dev boots + pet renders:
curl -s http://127.0.0.1:9222/json | python -c "import sys,json;d=json.load(sys.stdin);[print(t['url']) for t in d]"
# pick pet target id, attach CDP, evaluate:
typeof window.__deskpet_anim_overlay  // → "undefined"
typeof window.fastForwardIdle         // → "undefined"
typeof window.fakeEmotion             // → "undefined"
Object.keys(window.__deskpet_anim_debug)  // → only v1 5 keys
```

## Recommended fix (Phase 3)

Add to `tauri-app/src/components/Live2DCanvas.tsx` ~line 442 (inside the existing useEffect):

```ts
if (import.meta.env.DEV) {
  Object.defineProperty(w, "__deskpet_anim_overlay", {
    configurable: true,
    get: () => overlayRef.current,
  });
  Object.defineProperty(w, "__deskpet_anim_debug_v2", {
    configurable: true,
    get: () => overlayRef.current?.getV2Debug(),
  });
}
```

Add to `tauri-app/src/App.tsx` (DEV-only init block):

```ts
if (import.meta.env.DEV) {
  (window as any).fastForwardIdle  = (mins: number) => idleWatcherRef.current?.simulateAge(mins * 60_000);
  (window as any).fakeEmotion      = (em: EmotionCode) => liveRef.current?.setEmotion(em, performance.now());
  (window as any).fakeMilestone    = (kind: string, msg: string) => liveRef.current?.triggerCelebration("milestone", msg, performance.now());
  (window as any).fakeDND          = (active: boolean, reasons: string[]) => liveRef.current?.setDNDActive(active, reasons as any, performance.now());
}
```

Estimated: ~15 LOC, 1 vitest. After landing, round-2 of this QA pass can run all §3–§19 P0 in 1.5–2h.

## Additional findings during runtime

1. **Backend `chat_v2` tool stack initialisation failure** — toast `v2 stack not initialized` shown at top of pet window after attempting chat send. This is **unrelated to animation v2** (string collision; backend's `deskpet_tool_registry_v2 is None` branch at `backend/main.py:3631`). Blocks D1 main path via real LLM emotion field but doesn't affect animation FR scope. Recommend separate spawn_task to triage backend tool v2 registry init.
2. **Orphan backend on port 8100** from previous session (PID 29440, started 2026-05-25 01:26) prevented Tauri's bundled backend launcher from binding. Killed manually; dev session retried successfully via in-app "重试" button. Add to project踩过的坑 list.
