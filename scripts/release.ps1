# W5 (R17) — packaged release helper for DeskPet (Windows).
#
# Produces NSIS installer + MSI under src-tauri\target\release\bundle\,
# then (optionally) generates the latest.json manifest that the updater
# plugin fetches.
#
# Prerequisites (one-time):
#   1. npm i -g @tauri-apps/cli (already in devDependencies, tauri CLI ok).
#   2. Rust toolchain (MSVC).
#   3. tauri signer generate -w $env:USERPROFILE\.tauri\deskpet.key
#      → copy the printed PUBLIC key into tauri.conf.json > plugins.updater.pubkey
#      → set TAURI_SIGNING_PRIVATE_KEY to contents of the private key file
#        (and TAURI_SIGNING_PRIVATE_KEY_PASSWORD if you used a passphrase).
#
# Usage:
#   pwsh scripts\release.ps1 -Version 0.1.1
#   pwsh scripts\release.ps1 -Version 0.1.1 -NoSign        # skip signing
#
# Non-goals: this script does NOT push a GitHub release. Upload the bundle
# under assets and reference it from latest.json by hand — keeps credentials
# out of the project.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Version,
    [switch]$NoSign
)

$ErrorActionPreference = "Stop"

Push-Location "$PSScriptRoot\..\tauri-app"
try {
    Write-Host "[release] syncing package.json + tauri.conf.json versions -> $Version"
    # package.json
    $pkgPath = "package.json"
    $pkg = Get-Content $pkgPath -Raw | ConvertFrom-Json
    $pkg.version = $Version
    $pkg | ConvertTo-Json -Depth 100 | Set-Content $pkgPath -Encoding UTF8

    # tauri.conf.json
    $confPath = "src-tauri\tauri.conf.json"
    $conf = Get-Content $confPath -Raw | ConvertFrom-Json
    $conf.version = $Version
    $conf | ConvertTo-Json -Depth 100 | Set-Content $confPath -Encoding UTF8

    # Cargo.toml — hand edit the version line (ConvertFrom-Json can't parse TOML)
    $cargoPath = "src-tauri\Cargo.toml"
    $cargo = Get-Content $cargoPath
    $cargo = $cargo -replace '^version = ".*"$', "version = `"$Version`""
    $cargo | Set-Content $cargoPath -Encoding UTF8

    Write-Host "[release] running tauri build"
    if ($NoSign) {
        Write-Host "[release] --no-sign: artifacts will not be verifiable by the updater."
        npm run tauri -- build -- --bundles nsis msi
    } else {
        if (-not $env:TAURI_SIGNING_PRIVATE_KEY) {
            throw "TAURI_SIGNING_PRIVATE_KEY not set. Either export it or pass -NoSign."
        }
        npm run tauri -- build -- --bundles nsis msi
    }

    Write-Host "[release] done. Bundles under src-tauri\target\release\bundle\"
} finally {
    Pop-Location
}
