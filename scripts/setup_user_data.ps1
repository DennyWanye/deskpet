# P3-S6 + P3-S7 — First-run / dev provisioner.
#
# DeskPet separates user-mutable state (config, DBs, logs) from heavy
# regenerable assets (model weights):
#
#   %AppData%\deskpet\              — roaming; config.toml + data/*.db + logs/
#   %LocalAppData%\deskpet\models\  — non-roaming; multi-GB ASR/TTS weights
#   %LocalAppData%\deskpet\Cache\   — HF cache, scratch
#
# Running this script is idempotent:
#   1. Creates the directory tree above if missing.
#   2. Copies the bundled/repo `config.toml` into AppData if the user
#      doesn't have one yet (backend does this on first boot too, but
#      having it pre-seeded makes dry-runs painless).
#   3. If a repo-local `backend/models/` exists AND the user's models
#      dir is empty, creates a directory junction instead of copying.
#      This keeps dev iteration snappy — one download, one symlink,
#      every worktree gets the same models for free.
#
# Safe to re-run. Will NOT overwrite an existing config.toml or an
# existing non-empty models dir. Prints what it did / would do.
#
# Usage:
#   powershell scripts/setup_user_data.ps1           # do it
#   powershell scripts/setup_user_data.ps1 -WhatIf   # dry-run
#
# Exit codes: 0 = ok, 1 = hard error.

[CmdletBinding(SupportsShouldProcess = $true)]
param()

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot

$appData      = $env:APPDATA                 # roaming
$localAppData = $env:LOCALAPPDATA             # non-roaming

if (-not $appData)      { Write-Host "[setup] FATAL: %APPDATA% not set"      -ForegroundColor Red; exit 1 }
if (-not $localAppData) { Write-Host "[setup] FATAL: %LOCALAPPDATA% not set" -ForegroundColor Red; exit 1 }

$userData   = Join-Path $appData      "deskpet"
$userLocal  = Join-Path $localAppData "deskpet"
$userModels = Join-Path $userLocal    "models"
$userCache  = Join-Path $userLocal    "Cache"
$userLogs   = Join-Path $userData     "logs"
$userDataDb = Join-Path $userData     "data"

# --- 1. mkdir -p ------------------------------------------------------
foreach ($dir in @($userData, $userDataDb, $userLogs, $userLocal, $userCache)) {
    if (-not (Test-Path $dir)) {
        if ($PSCmdlet.ShouldProcess($dir, "New-Item -ItemType Directory")) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
            Write-Host "[setup] created  $dir"
        }
    } else {
        Write-Host "[setup] exists   $dir"
    }
}

# --- 2. seed config.toml ----------------------------------------------
$userConfig = Join-Path $userData "config.toml"
$bundleConfig = Join-Path $repoRoot "config.toml"

if (Test-Path $userConfig) {
    Write-Host "[setup] config   $userConfig (kept)"
} elseif (Test-Path $bundleConfig) {
    if ($PSCmdlet.ShouldProcess($userConfig, "Copy-Item from $bundleConfig")) {
        Copy-Item $bundleConfig $userConfig
        Write-Host "[setup] config   seeded from $bundleConfig" -ForegroundColor Green
    }
} else {
    Write-Host "[setup] config   WARN: no bundle config.toml at $bundleConfig" -ForegroundColor Yellow
}

# --- 3. models junction / copy ----------------------------------------
$repoModels = Join-Path $repoRoot "backend\models"

# Does userModels already contain real content?
$userModelsHasContent = $false
if (Test-Path $userModels) {
    $userModelsHasContent = @(Get-ChildItem $userModels -ErrorAction SilentlyContinue).Count -gt 0
}

if ($userModelsHasContent) {
    Write-Host "[setup] models   $userModels (populated, kept)"
} elseif (Test-Path $repoModels) {
    # Remove empty placeholder dir then junction. mklink /J is idempotent-ish:
    # fails if target exists, so we clean first.
    if (Test-Path $userModels) {
        if ($PSCmdlet.ShouldProcess($userModels, "Remove empty directory")) {
            Remove-Item $userModels -Force -Recurse -ErrorAction SilentlyContinue
        }
    }
    if ($PSCmdlet.ShouldProcess($userModels, "mklink /J -> $repoModels")) {
        # cmd.exe mklink /J works without admin (directory junctions don't need SeCreateSymbolicLink).
        & cmd.exe /c mklink /J "$userModels" "$repoModels" | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[setup] models   junctioned $userModels -> $repoModels" -ForegroundColor Green
        } else {
            Write-Host "[setup] models   FAIL: mklink /J returned $LASTEXITCODE" -ForegroundColor Red
            exit 1
        }
    }
} else {
    # Neither user nor repo has models. Leave the empty dir — user will
    # populate it later via downloader or manual drop.
    Write-Host "[setup] models   $userModels (empty; drop ASR/TTS weights here)" -ForegroundColor Yellow
}

# --- 4. summary -------------------------------------------------------
Write-Host ""
Write-Host "[setup] OK" -ForegroundColor Green
Write-Host "  user data : $userData"
Write-Host "  models    : $userModels"
Write-Host "  cache     : $userCache"
exit 0
