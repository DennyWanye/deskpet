$ErrorActionPreference = "Stop"
$repo = "/path/to/deskpet-ui-fixes-restore"
$backend = "$repo\backend"
$tauri = "$repo\tauri-app"
$userdata = "$backend\userdata-restore"

New-Item -ItemType Directory -Force -Path $userdata | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $userdata "data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $userdata "workspace") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $userdata "logs") | Out-Null

# Standard ports 8100/5173 — master orphan backend was killed before launch.
# This worktree has its own dist/ + node_modules/ symlinks pointing at master,
# so dev runs end-to-end without needing a fresh npm install.
$env:DESKPET_USER_DATA_DIR = $userdata
$env:DESKPET_DEV_MODE      = "1"
$env:DESKPET_PYTHON        = "/path/to/deskpet\backend\.venv\Scripts\python.exe"
$env:DESKPET_BACKEND_DIR   = $backend

Write-Host "==> Restore worktree dev — backend=8100 vite=5173" -ForegroundColor Cyan
Set-Location $tauri
npm run tauri dev
