# P2-0-S7 首次公测 release v0.2.0 — HANDOFF

**Date**: 2026-04-15
**Sprint**: V6 Phase 2 · Sprint P2-0 · Slice 7
**Status**: 🟡 Prep complete, awaiting user action before tag push
**Target version**: `v0.2.0`

## What shipped locally

All code + docs for the v0.2.0 release are committed on `master` and
pushed to `origin/master`. The release tag is **created locally but
not yet pushed** — that's the step that triggers the CI release
workflow, and CI will fail without the signing-key secret uploaded
first.

## Commits (master)

| SHA | Subject |
|---|---|
| `e663b5c` | release: bump version to 0.2.0 (P2-0-S7) |

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

## Local tag

```
v0.2.0 -> e663b5c (annotated)
```

## User actions required before `git push --tags`

1. **Upload the signing-key secret to GitHub repo secrets**
   (one-time — same instruction as P2-0-S2 handoff):

   ```powershell
   Get-Content $env:USERPROFILE\.tauri\deskpet.key -Raw | Set-Clipboard
   ```

   Then open
   <https://github.com/DennyWanye/deskpet/settings/secrets/actions>
   and add a new "Repository secret":
   - Name: `TAURI_SIGNING_PRIVATE_KEY`
   - Value: paste (Ctrl+V)

   The key has **no passphrase**, so `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`
   is NOT needed for this release.

2. **Push the tag:**

   ```bash
   git push origin v0.2.0
   ```

   This kicks `.github/workflows/release.yml` on windows-latest.

## Post-push verification plan

Once the CI run finishes (~15-25 min for a cold Windows cache):

1. Confirm the GitHub Release page lists four assets:
   - `DeskPet_0.2.0_x64-setup.exe`
   - `DeskPet_0.2.0_x64-setup.exe.sig`
   - `DeskPet_0.2.0_x64_en-US.msi`
   - `DeskPet_0.2.0_x64_en-US.msi.sig`
   - `latest.json` (synthesized in the workflow from the .sig + release URL).
2. On a Windows box with the current dev build (v0.1.0 in
   `tauri.conf.json`) installed: launch DeskPet, let the updater
   plugin hit
   `https://github.com/DennyWanye/deskpet/releases/latest/download/latest.json`,
   accept the prompt, confirm the installer runs and the app relaunches
   as 0.2.0.
3. If the updater fails: check the logs in-app → common causes are a
   pubkey mismatch (regenerated since the installed build was built)
   or network/proxy. The S2 handoff documents the expected
   `tauri.conf.json` pubkey.

## Gates

- ✅ `master` pushed to `origin/master` (no pending commits on master).
- ✅ Local tag `v0.2.0` created against `e663b5c`.
- ⏳ `TAURI_SIGNING_PRIVATE_KEY` GitHub secret — **user action**.
- ⏳ `git push origin v0.2.0` — pending above.
- ⏳ CI workflow green — pending above.
- ⏳ End-to-end self-update smoke — pending CI artifacts.

## Follow-ups

- After a successful v0.2.0 release and self-update rehearsal, rotate
  the signing key with a passphrase and upload both
  `TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`.
  Document the rotation in a short addendum on this handoff.
- Set up a release-notes template that cross-links the CHANGELOG
  section into the GitHub Release body. Today the workflow just
  publishes artifacts with the default GitHub-generated description.

## Spec / plan

- Roadmap entry:
  `docs/superpowers/plans/2026-04-14-phase2-v6-roadmap.md` §3.1 slice
  P2-0-S7
- Predecessor handoffs: `p2s1-icon-branding.md` → `p2s6-chat-history-a11y.md`
