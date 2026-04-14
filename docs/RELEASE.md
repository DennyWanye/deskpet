# Packaging & Release (W5 / R17)

## One-time setup

```powershell
# 1. Generate an Ed25519 signing keypair for the updater.
npx tauri signer generate -w $env:USERPROFILE\.tauri\deskpet.key

# 2. Copy the printed PUBLIC KEY into tauri-app/src-tauri/tauri.conf.json:
#    plugins.updater.pubkey
# 3. Export the private key (and passphrase if you set one) for CI or local builds:
$env:TAURI_SIGNING_PRIVATE_KEY = Get-Content $env:USERPROFILE\.tauri\deskpet.key -Raw
$env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = "..."
```

**Never commit the private key.** It belongs in your personal keystore / CI
secret vault, not the repo.

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

The updater plugin fetches `latest.json` from the endpoint configured in
`tauri.conf.json`. Template:

```json
{
  "version": "0.1.1",
  "notes": "Bug fixes and new tools.",
  "pub_date": "2026-04-14T12:00:00Z",
  "platforms": {
    "windows-x86_64": {
      "signature": "<contents of DeskPet_0.1.1_x64-setup.exe.sig>",
      "url": "https://github.com/YOUR_ORG/deskpet/releases/download/v0.1.1/DeskPet_0.1.1_x64-setup.exe"
    }
  }
}
```

Upload the installer + `latest.json` to the GitHub release, and the plugin
picks it up on next startup.

## Updater endpoint configuration

In `tauri-app/src-tauri/tauri.conf.json`:

```jsonc
"plugins": {
  "updater": {
    "active": true,
    "endpoints": [
      "https://github.com/YOUR_ORG/deskpet/releases/latest/download/latest.json"
    ],
    "dialog": true,
    "pubkey": "<public key printed by tauri signer generate>"
  }
}
```

Replace `YOUR_ORG/deskpet` with your actual repo and the `pubkey` with the
content printed by `tauri signer generate`. With `dialog: true` the
plugin shows the built-in OS prompt.

## Autostart

`@tauri-apps/plugin-autostart` is registered; it exposes `enable()` /
`disable()` / `isEnabled()` from the frontend. A settings toggle wired
to these functions is a Phase 2 item — nothing runs on login today
unless the frontend opts in.
