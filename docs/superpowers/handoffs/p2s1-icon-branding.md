# P2-0-S1 Icon Branding — HANDOFF

**Date**: 2026-04-14
**Sprint**: V6 Phase 2 · Sprint P2-0 · Slice 1
**Status**: ✅ Complete
**Target version**: feeds into `v0.2.0-phase2-beta1`

## What shipped

Replaced the red-square placeholder `icon.png` and the Vite-default
`favicon.svg` with a hand-written claymorphic purple cloud mascot.
Single SVG source, one-shot PowerShell pipeline, all derived artifacts
regeneratable.

## Commits (master)

| SHA | Subject |
|---|---|
| `d7e136c` | feat(branding): add placeholder cloud SVG source (P2-0-S1) |
| `73fa127` | feat(branding): add SVG→PNG renderer helper using @resvg/resvg-js |
| `054b1f1` | feat(branding): render placeholder cloud PNG (1024×1024) |
| `f1ae83c` | feat(branding): regenerate icon set from placeholder cloud (tauri icon) |
| `7503c2c` | feat(branding): replace Vite default favicon with cloud SVG |
| `c657392` | feat(branding): add rebuild-icons.ps1 pipeline (SVG -> PNG -> icon set -> favicon) |
| `bb0b93d` | docs(branding): document icons-src/ placeholder and regen pipeline |
| `9b8260c` | docs: note placeholder icon in RELEASE.md |

## Gates

- ✅ `npx tsc --noEmit` clean
- ✅ `cargo check` clean
- ✅ `rebuild-icons.ps1` idempotent (second run produces clean `git status`
  except for `icon.icns` — known quirk, documented in
  `tauri-app/src-tauri/icons-src/README.md` and `scripts/rebuild-icons.ps1`
  header. DeskPet is Windows-only through Phase 2; revisit when macOS
  support lands.)
- ✅ `icon.png` no longer a flat color (distinct-sample gate > 3,
  verified in Task 4)
- ⚠️ CDP E2E 4/5 PASS: `chat`, `memory`, `mic`, `dialog` all green.
  `esc` step FAILS — **pre-existing flake unrelated to this slice**.
  Root cause (traced in `backend/main.py:306-328`): chat handler is
  non-streaming (accumulates full response before sending one
  `chat_response` event), and the `interrupt` handler only forwards to
  the voice pipeline, not the chat text path. The esc E2E step relies
  on mid-stream interruption of a long chat reply; that path has not
  existed since the dialog-bar refactor (`0dd825e` / `3e65db1`).
  **Scope of this slice is cosmetic asset replacement; no code paths
  that could affect chat streaming were touched.** Follow-up slice
  should either restore streaming on the chat path or adjust/quarantine
  the esc E2E step to match current architecture.

## Success Criteria (spec §7)

- **C1 Not-flat-color** ✅ Task 4 Step 3, distinct-sample gate > 3
- **C2 Installer visual parity** — deferred to packaging step (Task 9
  extension); installer build (`tauri build --debug`) was optional and
  not run as part of this slice
- **C3 Small-size legibility** ✅ Visual inspection of 32×32 and 64×64
  outputs under `src-tauri/icons/` confirms cloud silhouette
  recognizable; 16×16 acceptable for placeholder. If a real brand asset
  later fails 16×16 legibility, open a hand-drawn override slice
  (spec §9 risk row 2)
- **C4 Commented SVG + README** ✅ Tasks 1 and 7
- **C5 Idempotent pipeline** ✅ Second run of `rebuild-icons.ps1`
  produces clean `git status` (modulo `.icns` quirk documented above)
- **C7 Favicon synced** ✅ `public/favicon.svg` is byte-identical to
  `deskpet-cloud.svg` after pipeline run

## Follow-ups

- Designer-produced real brand icon replaces `deskpet-cloud.svg` (tracked
  as an open item in V6 §3.1). Replacement procedure documented in
  `tauri-app/src-tauri/icons-src/README.md`.
- First release (`v0.2.0`) ships with this placeholder.
- Separate slice needed to address `esc` E2E failure — either restore
  chat-text streaming + interrupt on the dialog-bar path, or update the
  E2E assertion to match the current non-streaming chat architecture.
  Not part of the icon branding scope.
- macOS `.icns` non-determinism: revisit `rebuild-icons.ps1` to either
  pin the encoder or strip `.icns` from the fanout when macOS support
  lands.

## Spec

`docs/superpowers/specs/2026-04-14-p2s1-icon-branding-design.md`

## Plan

`docs/superpowers/plans/2026-04-14-p2s1-icon-branding.md`
