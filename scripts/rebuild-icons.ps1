#requires -Version 5.1
<#
.SYNOPSIS
    Rebuild the DeskPet icon set from the SVG source.

.DESCRIPTION
    Pipeline: SVG -> PNG (via render-svg.mjs) -> full icon set (via tauri icon)
    -> favicon.svg sync.

    Mostly idempotent: all PNG / ICO outputs are byte-identical across runs.
    KNOWN QUIRK: tauri icon's .icns encoder is non-deterministic (re-runs
    produce a different icon.icns even from the same PNG). Since DeskPet is
    Windows-only through Phase 2, .icns drift is cosmetic — check it in
    once and `git checkout -- tauri-app/src-tauri/icons/icon.icns` on any
    subsequent no-op re-run. If macOS support lands later, revisit.

    Compatible with Windows PowerShell 5.1 and PowerShell 7+.

.NOTES
    Run from repo root:
        pwsh scripts/rebuild-icons.ps1
        # or, on a box without PS7 installed:
        powershell -ExecutionPolicy Bypass -File scripts/rebuild-icons.ps1

    To replace the placeholder with a designer version:
        1. Overwrite tauri-app/src-tauri/icons-src/deskpet-cloud.svg OR
           overwrite deskpet-cloud.png (and skip Step 1 by editing the script).
        2. Run this script.
        3. Commit the regenerated icons/.
#>
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# Resolve repo root relative to this script so it works no matter where invoked.
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$TauriApp = Join-Path $RepoRoot "tauri-app"
$IconsSrc = Join-Path $TauriApp "src-tauri/icons-src"
$SvgSource = Join-Path $IconsSrc "deskpet-cloud.svg"
$PngSource = Join-Path $IconsSrc "deskpet-cloud.png"
$FaviconDst = Join-Path $TauriApp "public/favicon.svg"

if (-not (Test-Path $SvgSource)) {
    throw "SVG source not found: $SvgSource"
}

Write-Host "[1/3] Rendering SVG -> PNG (1024x1024)..."
Push-Location $TauriApp
try {
    node scripts/render-svg.mjs "src-tauri/icons-src/deskpet-cloud.svg" 1024 "src-tauri/icons-src/deskpet-cloud.png"
    if ($LASTEXITCODE -ne 0) { throw "render-svg.mjs failed with exit $LASTEXITCODE" }
} finally {
    Pop-Location
}

Write-Host "[2/3] Fanning out to platform icon set (tauri icon)..."
Push-Location $TauriApp
try {
    npx --yes @tauri-apps/cli icon "src-tauri/icons-src/deskpet-cloud.png"
    if ($LASTEXITCODE -ne 0) { throw "tauri icon failed with exit $LASTEXITCODE" }
} finally {
    Pop-Location
}

Write-Host "[3/3] Syncing favicon.svg..."
Copy-Item -Path $SvgSource -Destination $FaviconDst -Force

Write-Host ""
Write-Host "Done. Diff a relevant icon to confirm:"
Write-Host "    git diff --stat tauri-app/src-tauri/icons tauri-app/public/favicon.svg"
