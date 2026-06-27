$ErrorActionPreference = "Stop"
$repo = "/path/to/deskpet-fun-ux"
$backend = "$repo\backend"
$tauri = "$repo\tauri-app"
$userdata = "$backend\userdata-fun"

New-Item -ItemType Directory -Force -Path $userdata | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $userdata "data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $userdata "workspace") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $userdata "logs") | Out-Null

Get-NetTCPConnection -LocalPort 8100 -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
  Write-Host "Killing orphan on :8100 pid=$($_.OwningProcess)"
  Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Milliseconds 800

$env:DESKPET_USER_DATA_DIR = $userdata
$env:DESKPET_DEV_MODE      = "1"
$env:DESKPET_PYTHON        = "/path/to/deskpet\backend\.venv\Scripts\python.exe"
$env:DESKPET_BACKEND_DIR   = $backend

Write-Host "==> fun-ux worktree dev — backend=8100 vite=5173" -ForegroundColor Cyan
Set-Location $tauri
npm run tauri dev
