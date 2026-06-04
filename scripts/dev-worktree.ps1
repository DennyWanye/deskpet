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
# DESKPET_USER_DATA_DIR at a worktree-local dir. Defaults: 8200 / 5273.
#
# 2026-05-31 (companion-code-v2 fix): added the THREE missing lines that
# caused worktree dev to silently run the STALE PyInstaller exe instead of
# source `main.py` (same class of bug dev-start.ps1 §40-51 documents):
#   1. DESKPET_BACKEND_DIR  — backend_launch.rs Priority 1 "run from source"
#                             signal; absent → falls to target/debug/backend/
#                             deskpet-backend.exe (stale; missing new endpoints
#                             like /api/commands/help → 404).
#   2. DESKPET_PYTHON        — explicit .venv interpreter so FlagEmbedding /
#                             torch-cuda resolve (not system Python).
#   3. DESKPET_USER_DATA_DIR — backend/paths.py reads *_DIR; the old
#                             DESKPET_USER_DATA (no suffix) was never read →
#                             userdata isolation silently no-op'd.
#
# Usage (from anywhere):
#   powershell -File scripts/dev-worktree.ps1
#   powershell -File scripts/dev-worktree.ps1 -BackendPort 8500 -VitePort 5573
#   powershell -File scripts/dev-worktree.ps1 -PythonPath C:\path\to\python.exe
param(
    [int]$BackendPort = 8200,
    [int]$VitePort = 5273,
    # Optional explicit interpreter. Default: this worktree's backend/.venv;
    # git worktrees DON'T share the main repo's gitignored .venv, so a fresh
    # worktree may need this pointed at the main checkout's .venv.
    [string]$PythonPath = ""
)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$dataDir = Join-Path $root ".dev-userdata"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

# Resolve the Python interpreter. Probe order:
#   1. -PythonPath arg (explicit)
#   2. this worktree's backend/.venv
#   3. sibling main checkout's backend/.venv (worktrees live next to it)
$venvPy = ""
if ($PythonPath -and (Test-Path $PythonPath)) {
    $venvPy = $PythonPath
} elseif (Test-Path (Join-Path $backend ".venv\Scripts\python.exe")) {
    $venvPy = Join-Path $backend ".venv\Scripts\python.exe"
} else {
    # git worktree fallback: main checkout usually a sibling dir whose name
    # drops the worktree suffix. Probe a few common layouts.
    $candidates = @(
        (Join-Path (Split-Path -Parent $root) "deskpet\backend\.venv\Scripts\python.exe"),
        (Join-Path (Split-Path -Parent $root) "deskpet-private\backend\.venv\Scripts\python.exe")
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { $venvPy = $c; break }
    }
}
if (-not $venvPy) {
    Write-Host "ERROR: no Python interpreter found." -ForegroundColor Red
    Write-Host "  This worktree has no backend/.venv (git worktrees don't share it)." -ForegroundColor Yellow
    Write-Host "  Pass one explicitly:" -ForegroundColor Yellow
    Write-Host "    scripts/dev-worktree.ps1 -PythonPath <main-repo>\backend\.venv\Scripts\python.exe" -ForegroundColor Yellow
    exit 1
}

# Worktree-safe cleanup: kill ONLY processes holding THIS worktree's ports
# (never the other checkout's — that's the whole point of parallel dev).
foreach ($port in @($BackendPort, $VitePort)) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object {
            Write-Host "  freeing port $port (PID $($_.OwningProcess))" -ForegroundColor DarkGray
            Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
        }
}
Start-Sleep -Seconds 1

# Port + userdata isolation
$env:DESKPET_BACKEND_PORT = "$BackendPort"
$env:DESKPET_VITE_PORT = "$VitePort"
$env:DESKPET_USER_DATA_DIR = $dataDir   # *_DIR — the name backend/paths.py reads
# Run-from-source signals (backend_launch.rs Priority 1) — without these the
# Tauri webview spawns the STALE bundled exe instead of this worktree's source.
$env:DESKPET_BACKEND_DIR = $backend
$env:DESKPET_PYTHON = $venvPy
$env:DESKPET_DEV_MODE = "1"

Write-Host "=== DeskPet isolated dev (parallel-safe, source backend) ===" -ForegroundColor Cyan
Write-Host "  checkout     : $root"
Write-Host "  backend port : $BackendPort"
Write-Host "  vite port    : $VitePort"
Write-Host "  user data    : $dataDir"
Write-Host "  interpreter  : $venvPy" -ForegroundColor DarkGray
Write-Host "  backend dir  : $backend (source main.py, NOT bundled exe)" -ForegroundColor DarkGray
Write-Host ""

Set-Location (Join-Path $root "tauri-app")

# tauri.conf.json's devUrl is static (localhost:5173); patch it for this
# run so the Tauri webview loads the relocated vite dev server.
#
# 2026-05-31 fix: pass the override via a temp FILE, not an inline JSON
# string. PowerShell strips the inner double-quotes when forwarding
# `--config '{"build":...}'` to the native `npx tauri` exe, producing
# `{build:{devUrl:...}}` → "key must be a string" parse error. A file
# path sidesteps the quoting entirely. (The old inline form never worked.)
$cfgFile = Join-Path $env:TEMP "deskpet-devurl-$VitePort.json"
Set-Content -Path $cfgFile -Encoding ascii -Value (
    '{"build":{"devUrl":"http://localhost:' + $VitePort + '"}}'
)
try {
    & npx tauri dev --config $cfgFile
} finally {
    Remove-Item $cfgFile -ErrorAction SilentlyContinue
}
