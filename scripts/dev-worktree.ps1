# Parallel-dev launcher — runs THIS checkout's DeskPet dev stack on
# isolated ports + an isolated user-data dir, so it never collides with
# another running checkout (e.g. the main repo and a git worktree both
# running `tauri dev` at the same time).
#
# Without this, two checkouts fight over:
#   - backend port 8100  (second backend fails to bind)
#   - vite port 5173     (strictPort: true → second vite exits)
#   - the shared user-data dir (%AppData%\deskpet or $DESKPET_USER_DATA)
#     → two backends writing the same state.db = lock contention / corruption
#
# This script sets DESKPET_BACKEND_PORT / DESKPET_VITE_PORT (honoured by
# process_manager.rs, backend/config.py, vite.config.ts) and points
# DESKPET_USER_DATA at a worktree-local dir. Defaults: 8200 / 5273.
#
# Usage (from anywhere):
#   powershell -File scripts/dev-worktree.ps1
#   powershell -File scripts/dev-worktree.ps1 -BackendPort 8300 -VitePort 5373
param(
    [int]$BackendPort = 8200,
    [int]$VitePort = 5273
)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$dataDir = Join-Path $root ".dev-userdata"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

$env:DESKPET_BACKEND_PORT = "$BackendPort"
$env:DESKPET_VITE_PORT = "$VitePort"
$env:DESKPET_USER_DATA = $dataDir

Write-Host "=== DeskPet isolated dev (parallel-safe) ===" -ForegroundColor Cyan
Write-Host "  checkout     : $root"
Write-Host "  backend port : $BackendPort"
Write-Host "  vite port    : $VitePort"
Write-Host "  user data    : $dataDir"
Write-Host ""

Set-Location (Join-Path $root "tauri-app")

# tauri.conf.json's devUrl is static (localhost:5173); patch it for this
# run so the Tauri webview loads the relocated vite dev server.
$cfg = '{"build":{"devUrl":"http://localhost:' + $VitePort + '"}}'
& npx tauri dev --config $cfg
