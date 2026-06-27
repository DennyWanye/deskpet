# P2-0-S7 首次公测 release v0.2.0 — HANDOFF

**Date**: 2026-04-15 (finalized)
**Sprint**: V6 Phase 2 · Sprint P2-0 · Slice 7
**Status**: ✅ **DONE** — v0.2.0 published on GitHub, artifacts verified
**Released version**: `v0.2.0`
**Release URL**: <https://github.com/DennyWanye/deskpet/releases/tag/v0.2.0>
**Published at**: `2026-04-15T03:58:29Z`
**Tag commit**: `718d70a` (retagged after the `createUpdaterArtifacts` fix)

## What shipped

All P2-0 code + docs for v0.2.0 are on `origin/master` and the
release tag `v0.2.0` is pushed. CI `release.yml` ran green on
`windows-latest` and produced the 5 expected artifacts, all
downloadable from the Release page.

**Assets on the Release page (verified 2026-04-15 via GitHub API):**

| Asset | Size |
|---|---|
| `DeskPet_0.2.0_x64-setup.exe` | 9 421 475 B |
| `DeskPet_0.2.0_x64-setup.exe.sig` | 416 B |
| `DeskPet_0.2.0_x64_en-US.msi` | 10 883 072 B |
| `DeskPet_0.2.0_x64_en-US.msi.sig` | 416 B |
| `latest.json` | 722 B |

## Commits (master)

| SHA | Subject |
|---|---|
| `e663b5c` | release: bump version to 0.2.0 (P2-0-S7) |
| `b5034c0` | docs: P2-0-S7 handoff (v0.2.0 release prep) |
| `334efc1` | fix(ci): make release workflow actually fail when tauri build fails |
| `718d70a` | fix(updater): enable createUpdaterArtifacts so CI emits .sig files |
| `cece634` | docs: add STATE.md for cross-session context rehydration |

CI debugging required two quick follow-up fixes after the initial tag
push (that's what the retag captures). The two root causes are now
encoded as environment gotchas in `STATE.md`.

(Plus every S1..S6 commit already merged — see each slice's handoff.)

## Version bumps

- `tauri-app/src-tauri/tauri.conf.json` → `0.2.0`
- `tauri-app/src-tauri/Cargo.toml` (`deskpet` crate) → `0.2.0`
- `tauri-app/src-tauri/Cargo.lock` (matching entry) → `0.2.0`
- `tauri-app/package.json` **left at `0.0.0`** — that package is
  `"private": true`, never published, and the version field is only
  cosmetic there. Skipping keeps the diff minimal.

## CHANGELOG.md

New top-level `CHANGELOG.md` covers every slice in Sprint P2-0 (S1
icon branding, S2 updater, S3 multi-session memory UI, S4 perf
scripts, S5 VN NIT cleanup, S6 a11y). Sections: Added / Changed /
Fixed / Security / Known issues, per Keep a Changelog conventions.

Noted as a Security follow-up: **rotate the signing key with a
non-empty passphrase before wider distribution**. Today's key has an
empty passphrase so CI doesn't need a password secret.

## Released tag

```
v0.2.0 -> 718d70a (annotated, pushed)
```

The tag was retagged once after the initial push: the first CI run
(`e663b5c`) silently skipped `.sig` emission because
`bundle.createUpdaterArtifacts` defaulted to `false` on Tauri 2. The
second run (`334efc1`) made the workflow surface that failure loudly
instead of publishing a half-signed release. The final run
(`718d70a`) produced the full 5-asset Release that's live today.

## Post-push verification (completed)

- ✅ GitHub Release page lists the 5 expected assets (see table
  above). Asset sizes look reasonable; `.sig` files are the expected
  416-byte Ed25519 signature format; `latest.json` synthesized by
  `release.yml`.
- 🟡 **End-to-end self-update smoke is a one-shot one-way test that
  has been deferred** — v0.2.0 ships with the **old** pubkey
  (`609610CD2AB388D1`) baked into the installer, and the next
  release's signing key was already rotated
  (`5F623E5CDBAA4C5A`, tracked in P2-0-S8). A v0.2.0→v0.2.x self-update
  will deliberately fail the signature check; v0.2.0 users must
  reinstall manually once. The rehearsal is therefore punted to the
  first v0.2.x → v0.2.(x+1) update cycle; see the `Follow-ups` section
  below and `p2s8-key-rotation.md`.

## Gates

- ✅ `master` pushed to `origin/master` at the tag time.
- ✅ Tag `v0.2.0` pushed (retagged to `718d70a`).
- ✅ `TAURI_SIGNING_PRIVATE_KEY` GitHub secret uploaded.
- ✅ CI workflow green (final run on `718d70a`).
- ✅ Release assets verified on GitHub API (5/5).
- 🟡 v0.2.0 → v0.2.x self-update smoke — **intentionally deferred**
  (see above).

## Follow-ups

These are tracked as open items in `STATE.md` "Pending follow-ups"
and are **not** blockers for closing P2-0-S7:

1. **v0.2.0 → v0.2.x self-update test** — run on first `v0.2.x`
   release after key rotation; expected to fail for v0.2.0 boxes
   (pubkey mismatch) and must succeed for v0.2.1→v0.2.2 boxes.
2. **Release-notes template** — workflow currently publishes with
   GitHub-generated notes. Should cross-link the relevant CHANGELOG
   section. Small chore, no rush.
3. **First `v0.2.x` after rotation** — CHANGELOG note must explain
   that v0.2.0 users need a one-time manual reinstall (pubkey rotation
   by design; see `p2s8-key-rotation.md`).

## Lessons learned (encoded elsewhere)

- `bundle.createUpdaterArtifacts: true` is mandatory on Tauri 2 —
  without it builds skip signing silently even with the key in env.
  (See `STATE.md` "Environment gotchas".)
- PowerShell swallows native command exit codes unless you set
  `$PSNativeCommandUseErrorActionPreference = $true` AND explicitly
  `if ($LASTEXITCODE -ne 0) { throw }` after each native call. The CI
  workflow now does both.
- Be willing to retag. Two broken CI runs cost us ~30 min total; a
  "never retag" rule would have locked us into shipping a broken
  release. Moving the annotated tag is fine for a public beta
  *before* any other consumer depends on it.

## Spec / plan

- Roadmap entry:
  `docs/superpowers/plans/2026-04-14-phase2-v6-roadmap.md` §3.1 slice
  P2-0-S7
- Predecessor handoffs: `p2s1-icon-branding.md` → `p2s6-chat-history-a11y.md`
