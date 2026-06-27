# Packaging & Release (W5 / R17)

## Status: signing key rotated to passphrase-protected key (P2-0-S8, 2026-04-15)

An Ed25519 (minisign) keypair protects the Tauri updater. The private
key lives at `%USERPROFILE%\.tauri\deskpet.key` on the release
maintainer's machine **and** in the repo's `TAURI_SIGNING_PRIVATE_KEY`
Actions secret — never in git. The matching passphrase is stored in
the `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` Actions secret.

The public key is baked into `tauri-app/src-tauri/tauri.conf.json`
under `plugins.updater.pubkey`. From **v0.2.1 onward** the public key
is effectively locked: every future release's `.sig` file must be
signed with the matching private key, or installed clients will
reject the update.

### Key rotation history

| Date | Key ID (first 16 hex) | Passphrase | Reason |
|---|---|---|---|
| 2026-04-15 (early) | `609610CD2AB388D1` | none | Initial P2-0-S2 keypair; shipped with `v0.2.0` |
| 2026-04-15 (late)  | `5F623E5CDBAA4C5A` | ✅ | P2-0-S8 rotation; adds passphrase before real user base exists |

The pre-rotation `v0.2.0` build has the **old** `609610CD...` public key
baked in. Clients running v0.2.0 therefore cannot self-update past the
rotation — they must manually install v0.2.1 (or later) once, after
which the new `5F623E5C...` pubkey takes over and self-update resumes
normally. This was judged acceptable because v0.2.0 had no real
external users.

The pre-rotation private key is retained at
`%USERPROFILE%\.tauri\deskpet.key.v0.2.0.bak` in case a hotfix signed
against the old pubkey is ever needed for stranded v0.2.0 installs.

## One-time setup (already done for this repo)

```powershell
# 1. Generate an Ed25519 signing keypair for the updater.
#    --ci skips the interactive password prompt;
#    --password "" omits the passphrase (regenerate before v0.2.0 to add one).
npx @tauri-apps/cli signer generate --ci --password "" `
    -w "$env:USERPROFILE\.tauri\deskpet.key"

# 2. Copy the printed PUBLIC KEY into tauri-app/src-tauri/tauri.conf.json:
#    plugins.updater.pubkey

# 3. For CI: upload the private-key file contents to the repo as the
#    TAURI_SIGNING_PRIVATE_KEY secret (and _PASSWORD if you set one).

# 4. For local signed builds, export the key into the environment:
$env:TAURI_SIGNING_PRIVATE_KEY = Get-Content $env:USERPROFILE\.tauri\deskpet.key -Raw
# $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = "..."  # only if you set one
```

**Never commit the private key.** It belongs in your personal keystore / CI
secret vault, not the repo.

## Rotating the signing key

Rotating the pubkey **breaks self-update for every installed client
running the pre-rotation build** — those clients still trust the old
pubkey, so they will reject anything signed by the new key. Only
rotate when (a) no real users are on the current pubkey yet, or (b)
you are willing to ask existing users to manually reinstall once.

Procedure (use the interactive form — do NOT pass `--password` on the
command line; that puts the passphrase in shell history):

```powershell
# 0. Back up the outgoing keypair in case a hotfix for pre-rotation
#    clients is ever needed.
Move-Item "$env:USERPROFILE\.tauri\deskpet.key" `
          "$env:USERPROFILE\.tauri\deskpet.key.<prev-version>.bak"
Move-Item "$env:USERPROFILE\.tauri\deskpet.key.pub" `
          "$env:USERPROFILE\.tauri\deskpet.key.pub.<prev-version>.bak"

# 1. Regenerate interactively — the CLI will prompt for the new
#    passphrase twice (input is not echoed).
npx @tauri-apps/cli signer generate `
    -w "$env:USERPROFILE\.tauri\deskpet.key"

# 2. Paste the new contents of deskpet.key.pub into
#    tauri-app/src-tauri/tauri.conf.json > plugins.updater.pubkey

# 3. Update the repo secrets at
#    https://github.com/DennyWanye/deskpet/settings/secrets/actions
#      TAURI_SIGNING_PRIVATE_KEY          = contents of deskpet.key
#      TAURI_SIGNING_PRIVATE_KEY_PASSWORD = new passphrase
```

After rotation, append an entry to the "Key rotation history" table
above and open a handoff doc recording the rationale.

## CI-driven releases (primary path)

The `.github/workflows/release.yml` workflow fires on any `v*.*.*`
tag push. Flow:

```
git tag v0.2.0
git push origin v0.2.0
# → Actions runs release.ps1 → bundles + signs → uploads
#   installer + .sig + latest.json to GitHub Release
```

Required repo secrets (Settings → Secrets and variables → Actions):

| Secret | Required | Notes |
|---|---|---|
| `TAURI_SIGNING_PRIVATE_KEY` | ✅ | Contents of `~/.tauri/deskpet.key` (entire file) |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | ✅ | Passphrase for the current key (required since P2-0-S8 rotation) |

The updater endpoint is already wired to
`https://github.com/DennyWanye/deskpet/releases/latest/download/latest.json`,
so once the workflow publishes a Release with `latest.json`, the
Tauri updater plugin will pick it up on next app start.

## Build

```powershell
# Bump to next version and bundle (NSIS .exe + MSI .msi).
pwsh scripts\release.ps1 -Version 0.1.1

# Skip signing (dev/local smoke tests only — updater won't verify these).
pwsh scripts\release.ps1 -Version 0.1.1 -NoSign
```

Artifacts land in `tauri-app/src-tauri/target/release/bundle/`:
```
bundle/nsis/DeskPet_0.1.1_x64-setup.exe          # user-mode installer
bundle/nsis/DeskPet_0.1.1_x64-setup.exe.sig      # signature
bundle/msi/DeskPet_0.1.1_x64_en-US.msi           # MSI for enterprise
bundle/msi/DeskPet_0.1.1_x64_en-US.msi.sig
```

## Publish update manifest

The CI workflow (`.github/workflows/release.yml`) generates this file
automatically and uploads it to the GitHub Release alongside the
installer. For manual publishing (offline / emergency), the shape is:

```json
{
  "version": "0.2.0",
  "notes": "Bug fixes and new tools.",
  "pub_date": "2026-04-14T12:00:00Z",
  "platforms": {
    "windows-x86_64": {
      "signature": "<contents of DeskPet_0.2.0_x64-setup.exe.sig>",
      "url": "https://github.com/DennyWanye/deskpet/releases/download/v0.2.0/DeskPet_0.2.0_x64-setup.exe"
    }
  }
}
```

Upload the installer + `latest.json` to the GitHub release, and the plugin
picks it up on next startup.

## Updater endpoint configuration (current)

In `tauri-app/src-tauri/tauri.conf.json`:

```jsonc
"plugins": {
  "updater": {
    "active": true,
    "endpoints": [
      "https://github.com/DennyWanye/deskpet/releases/latest/download/latest.json"
    ],
    "dialog": true,
    "pubkey": "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IEJERjExMTNERkY4QjQ3MTMK..."
  }
}
```

The `pubkey` above is the base64 minisign public key emitted by
`tauri signer generate` and is safe to ship in the client binary.
With `dialog: true` the plugin shows the built-in OS update prompt.

## Autostart

`@tauri-apps/plugin-autostart` is registered; it exposes `enable()` /
`disable()` / `isEnabled()` from the frontend. A settings toggle wired
to these functions is a Phase 2 item — nothing runs on login today
unless the frontend opts in.

## Branding assets

The current app icon is a **temporary placeholder** (a claymorphic
purple cloud mascot) committed in V6 Phase 2 Sprint P2-0 Slice 1. It
is stored in SVG form at
`tauri-app/src-tauri/icons-src/deskpet-cloud.svg`; all derived assets
(`icon.ico`, `icon.png`, the per-platform subdirectories, and
`public/favicon.svg`) are produced by `scripts/rebuild-icons.ps1`.

To replace the placeholder with a designer-produced icon, see
`tauri-app/src-tauri/icons-src/README.md`.

Before the first public release, the placeholder **should** be replaced
with a real brand asset; it is intentionally cute and visually distinct
from the previous red-square bug, but it is not a committed brand mark.
