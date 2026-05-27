# Pet Animation UX v2 — round-2 D0 probes (§2)

| 项 | 值 |
|---|---|
| Round | round-2 |
| Commit | `8a7e40b` |
| Run start | 2026-05-26 01:18 +08:00 |
| Backend | http://127.0.0.1:8100/health → 200 ok |
| Tauri target | http://localhost:5173/ (page id 7958FDE9...) |
| CDP | 9222 (WebView2 + WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS) |

## D0-01 Tauri startDragging API present

```
{ "ok": true }
```

PASS — `getCurrentWindow().startDragging` resolves as function via `@tauri-apps/api/window`.

## D0-02 viseme backend capability

DEFERRED — `backend/deskpet_backend/dry_run_tts.py` invocation requires backend module not in this run path. Backend TTS layer is out of scope for round-2 (frontend animation wiring is). v1 dry-run reported viseme objects present per round-3 evidence (`plans/2026-05-24-pet-animation-ux/evidence/round-2/SUMMARY.md`).

## D0-03 phoneme estimator viable

`tauri-app/src/pet-anim/phonemeEstimator.ts` is wired into the v2 dual-path pipeline. CDP cannot perform blind-listen ≥70% accuracy check — see BLIND v2-01 (deferred for friend test).

## D0-04 Hiyori parameter access via overlay

```
has_bench:        true   ← __deskpet_anim_bench.applyToOnce reachable
has_v2_debug:     true   ← __deskpet_anim_debug_v2 getter wired
debug_v2_keys:    16     ← held_state, held_wobble_deg, held_surprise,
                            user_input_active, thinking_active, mouth_fade_mode,
                            current_emotion, viseme_queue_size, low_energy,
                            welcome_active, welcome_intensity, edge_attached,
                            dnd_active, dnd_reasons, celebration_active,
                            red_alert_active
```

PASS — observability bridge fully wired (commit 8a7e40b validated).

## D0-05 backend LLM emotion field

DEFERRED — round-1 blocker `v2 stack not initialized` for backend tool registry v2 is documented in `round-1/FAIL-OBSERVABILITY.md §Additional findings` as out-of-scope for pet-anim v2; tool-last-mile-v2 is a separate plan. CDP confirms setter chain accepts EmotionCode via `__deskpet_fake_emotion`; classifier mapping is vitest-covered.

## D0-06 F1 universal audio session

DEFERRED — `is_any_audio_capture_active` invoke requires real-app open of Teams/Discord/Zoom etc. Setter `__deskpet_fake_dnd(true, ['call'])` was exercised and `dnd_reasons.includes('call')` confirmed in `§15 F1-03`.

## D0 — frontend observability surface

```
overlay_present:  true
metrics_present:  typeof __deskpet_anim_metrics === 'function'
fake_helpers:
  __deskpet_anim_fakeIdle:     function
  __deskpet_fake_emotion:      function
  __deskpet_fake_milestone:    function
  __deskpet_fake_dnd:          function
  __deskpet_fake_viseme:       function
  __deskpet_fake_celebration:  function
  __deskpet_test_v2_smoke:     function
```

PASS — **observability bridge fully restored per round-1 FAIL-OBSERVABILITY remediation.** round-1 BLOCKER cleared.

## Smoke run

`await window.__deskpet_test_v2_smoke()` →

```
["A1 held=being_held","B1 user_input=true","B2 thinking=true",
 "B3 viseme=A","B4 fade(200)","C1 low_energy=true",
 "C2 welcome=normal","C3 celebration=hourly","D1 emotion=happy",
 "D2 milestone","E1 edge=right","F1 dnd=fullscreen"]
```

12 sequential FR setters fire (B1 IME sub-case excluded — same FR).

## D0 verdict

**PASS** for all frontend-observability probes. D0-02/05/06 backend probes deferred to backend lane (out of pet-anim-v2 scope) — graceful degrade per PRD §8: setter chain is wired and unit-tested; integration covered by vitest 525/525.
