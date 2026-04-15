# P2-0-S2 Updater 密钥对 — HANDOFF

**Date**: 2026-04-15
**Sprint**: V6 Phase 2 · Sprint P2-0 · Slice 2
**Status**: ✅ Code complete · ⏳ Repo Secret upload pending (user action)
**Target version**: feeds into `v0.2.0-phase2-beta1`

## What shipped

- Generated an Ed25519 (miniSign) updater keypair, rotated once after
  the initial key was inadvertently printed to chat log. Private key
  lives at `%USERPROFILE%\.tauri\deskpet.key` on the maintainer
  machine; public key baked into `tauri.conf.json`.
- Pointed the updater endpoint at
  `https://github.com/DennyWanye/deskpet/releases/latest/download/latest.json`.
- Added `.github/workflows/release.yml`: any `v*.*.*` tag push spins
  up `windows-latest`, runs `scripts/release.ps1`, synthesizes
  `latest.json`, and uploads the signed installer + MSI + manifest to
  a GitHub Release.
- Hardened `.gitignore` against `*.key` / `.tauri/`.
- Rewrote `docs/RELEASE.md` around the CI-driven path; documented the
  passphrase deferral and the safe key-rotation window (pre-v0.2.0).
- Initialized the remote at `git@github.com:DennyWanye/deskpet.git`
  and pushed the full history (master).

## Commits (master)

| SHA | Subject |
|---|---|
| `fc3e2ee` | feat(release): wire updater signing key + GitHub Actions release pipeline |
| `b35b1e7` | fix(release): rotate updater signing key (previous pubkey leaked in chat) |

Remote push: `master` tracks `origin/master` at
`git@github.com:DennyWanye/deskpet.git`.

## Current updater configuration

- Endpoint: `https://github.com/DennyWanye/deskpet/releases/latest/download/latest.json`
- Pubkey (`tauri.conf.json > plugins.updater.pubkey`):
  `dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IDYwOTYxMENEMkFCMzg4RDEK...`
- Passphrase: **none** — deferred (see follow-ups)

## Gates

- ✅ `npx tsc --noEmit` clean
- ✅ `cargo check` clean
- ✅ `git push -u origin master` succeeded; remote repo confirmed
- ⏳ End-to-end self-update validation (`v0.1.1-test` → `v0.2.0`)
  **blocked on Secret upload + S7**

## Pending user action

To unblock the CI release workflow, the maintainer must upload the
private key as a repo secret. This is a one-time step.

1. Open https://github.com/DennyWanye/deskpet/settings/secrets/actions
2. "New repository secret"
3. Name: `TAURI_SIGNING_PRIVATE_KEY`
4. Value: entire file contents of `C:\Users\24378\.tauri\deskpet.key`
   - PowerShell helper:
     `Get-Content $env:USERPROFILE\.tauri\deskpet.key -Raw | Set-Clipboard`
5. (Later, after passphrase rotation — see follow-ups) add
   `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` the same way.

## Follow-ups

- **Before `v0.2.0` ships:** regenerate the key **with a passphrase**
  and swap the new pubkey into `tauri.conf.json`. Safe while no signed
  release has been published. Rotation commands documented in
  `docs/RELEASE.md` § Rotating the signing key. Once `v0.2.0` ships,
  the key is locked.
- **Upload `TAURI_SIGNING_PRIVATE_KEY` Actions secret** (see above).
- **End-to-end self-update rehearsal** — cut a `v0.1.1-test` tag,
  confirm the workflow produces a signed installer + `latest.json`,
  install it, then cut `v0.1.2-test` and verify the in-app updater
  prompt + apply path. Folded into S7.

## Spec / plan

- Roadmap entry: `docs/superpowers/plans/2026-04-14-phase2-v6-roadmap.md`
  §3.1 slice P2-0-S2
- Decision: **D0-2 = GitHub Releases** (user signoff, 2026-04-15)
- Decision: passphrase deferred (user signoff, 2026-04-15) —
  re-evaluate before first signed release
