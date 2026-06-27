# P2-0-S8 Updater 签名密钥轮换 — HANDOFF

**Date**: 2026-04-15
**Sprint**: V6 Phase 2 · Sprint P2-0 · Slice 8 (post-release security follow-up)
**Status**: ✅ Complete — new passphrase-protected key live
**Target version**: takes effect on the next `v0.2.x` tag push (no release cut in this slice)

## Why this slice existed

`v0.2.0` shipped with an Ed25519 updater key that had **no passphrase**
(P2-0-S2 explicitly deferred the passphrase to avoid blocking the
first public beta). That was acceptable while the key was only in
maintainer-local storage + one GitHub Actions secret, but "single
layer of defense on the key that authorizes every future auto-update"
is a standing liability. This slice closes it before the user base
grows beyond a single maintainer.

## What shipped

- Generated a fresh Ed25519 (minisign) keypair **with a non-empty
  passphrase**, interactively (no `--password` on the command line, so
  the passphrase never hit shell history).
- Swapped the new public key into
  `tauri-app/src-tauri/tauri.conf.json > plugins.updater.pubkey`.
- Rewrote the opening + "Rotating the signing key" sections of
  `docs/RELEASE.md`:
  - Removed the "cannot be rotated once v0.2.0 ships" language
    (overtaken by this slice).
  - Added a Key rotation history table.
  - Flipped the Secrets table so `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`
    is now ✅ required rather than "conditional".
  - Replaced the `--ci --password "..."` rotation example with the
    interactive form.
- Uploaded / updated the GitHub Actions secrets
  (`TAURI_SIGNING_PRIVATE_KEY` overwritten, `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`
  added).
- Retained the pre-rotation keypair as
  `%USERPROFILE%\.tauri\deskpet.key(.pub).v0.2.0.bak` in case a hotfix
  signed against the old pubkey is ever needed for a stranded v0.2.0
  install.

## Key rotation ledger

| Date | Key ID (first 16 hex) | Passphrase | Shipped in | Status |
|---|---|---|---|---|
| 2026-04-15 (early) | `609610CD2AB388D1` | none | `v0.2.0` | Retired; private key retained locally as `.v0.2.0.bak` |
| 2026-04-15 (late)  | `5F623E5CDBAA4C5A` | ✅ | `v0.2.1+` (next tag) | **Active** |

Public key fingerprint (safe to cite):

```
dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IDVGNjIzRTVDREJBQTRDNUEKUldSYVRLcmJYRDVpWHlMN1d1bjNwVE5SdGNqQXFLdml5THkvZHV6cTNrOFQwN2hXVk92YkgzaEQK
```

## Known breakage (intentional, accepted)

Any client that is already running the `v0.2.0` installer has the old
`609610CD...` pubkey baked in. That client **cannot self-update past
this rotation** — the next version's `.sig` will be signed by the new
`5F623E5C...` private key, which the old build will reject with a
signature-verification error.

Mitigation:

- Decision was explicitly taken with maintainer confirmation that
  **no real external users are on v0.2.0** (2026-04-15). The only
  known v0.2.0 installs are maintainer test machines, which can do a
  one-time manual reinstall of v0.2.1 when it ships.
- If a stranded v0.2.0 install is ever reported, a v0.2.0.x hotfix
  can still be signed by the retained `.v0.2.0.bak` private key to
  nudge that user forward. That is an emergency-only path.

## Files changed

| File | Change |
|---|---|
| `tauri-app/src-tauri/tauri.conf.json` | `plugins.updater.pubkey` → new key |
| `docs/RELEASE.md` | Status section, Rotation section, Secrets table |

No version bump, no tag push, no CHANGELOG entry in this slice — the
rotation takes effect whenever the next `v0.2.x` is cut. That will be
the de-facto end-to-end verification (new CI run will fetch the new
`TAURI_SIGNING_PRIVATE_KEY_PASSWORD` secret, decrypt the new private
key, and produce `.sig` files signed by `5F623E5C...`).

## GitHub secrets state (confirmed 2026-04-15)

| Secret | State |
|---|---|
| `TAURI_SIGNING_PRIVATE_KEY` | ✅ updated to contents of new `~/.tauri/deskpet.key` |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | ✅ newly added, holds the new passphrase |

`release.yml` already referenced `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`
from P2-0-S2 — no workflow change needed for this rotation.

## Local maintainer state

- `%USERPROFILE%\.tauri\deskpet.key` — new private key (passphrase-encrypted)
- `%USERPROFILE%\.tauri\deskpet.key.pub` — new public key
- `%USERPROFILE%\.tauri\deskpet.key.v0.2.0.bak` — previous private key
- `%USERPROFILE%\.tauri\deskpet.key.pub.v0.2.0.bak` — previous public key

Passphrase location: maintainer's password manager (not in this repo,
not in any chat transcript, not echoed to any log).

## Gates

- ✅ `tauri.conf.json` parses as valid JSON (Edit tool succeeded; no
  manual structural changes).
- ✅ `docs/RELEASE.md` content pass — internal cross-references still
  resolve, rotation table + secrets table in sync.
- ✅ GitHub secrets pair present.
- ⏳ End-to-end signature validation — deferred to the next `v0.2.x`
  release cut (expected as part of whatever P2-1 slice triggers one).

## Follow-ups

- **Next time a `v0.2.x` tag is pushed**, watch the CI logs for the
  "Build signed release bundle" step — the first real signature with
  the new key should appear there. If the passphrase secret is wrong,
  tauri will abort during signing with a decryption error.
- Add a brief CHANGELOG entry in the next release noting the pubkey
  rotation, so anyone manually installing v0.2.1 over v0.2.0
  understands why in-app self-update won't work until they do.
- Roll back the v0.2.0 follow-up item in
  `docs/superpowers/STATE.md` (done in this slice).

## Spec / plan

- Predecessor handoffs: `p2s2-updater-keypair.md` (original key),
  `p2s7-release-v0.2.0.md` (identified this as the Security
  follow-up).
- Roadmap entry: none — this is a post-P2-0 security tidy-up, not a
  planned sprint slice. Recorded here to keep the handoff chain
  contiguous.
