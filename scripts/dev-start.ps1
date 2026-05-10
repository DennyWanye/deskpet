# DeskPet dev mode launcher.
#
# Runs backend + Tauri webview against the local source tree (no MSI
# rebuild needed). Iteration loop: edit source → reload (Ctrl+R for
# frontend, restart this script for backend changes).
#
# Both processes write to repo-local userdata/ via DESKPET_USER_DATA_DIR
# so dev runs DON'T pollute %AppData%/deskpet/.

$ErrorActionPreference = "Stop"

$repo = (Resolve-Path "$PSScriptRoot\..").Path
$backend = Join-Path $repo "backend"
$tauri = Join-Path $repo "tauri-app"
$userdata = Join-Path $backend "userdata"

# Ensure portable userdata exists so MCP filesystem + SessionDB have
# a real directory on first launch.
New-Item -ItemType Directory -Force -Path $userdata | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $userdata "data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $userdata "workspace") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $userdata "logs") | Out-Null

# Kill any prior dev backend/Tauri so port 8100 is free.
Get-Process | Where-Object { $_.ProcessName -in @("python","deskpet","deskpet-backend") } |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

Write-Host "==> Starting backend in a new console window..." -ForegroundColor Cyan
$env_args = @{
    DESKPET_USER_DATA_DIR = $userdata
    DESKPET_DEV_MODE      = "1"
}
$envCmd = ($env_args.GetEnumerator() | ForEach-Object { "`$env:$($_.Key)='$($_.Value)'" }) -join "; "
Start-Process -FilePath "powershell" `
    -ArgumentList "-NoExit", "-Command", "$envCmd; cd '$backend'; python main.py" `
    -WorkingDirectory $backend

# Backend takes ~5-8s to bind 8100; give it a head start so the
# Tauri webview's WebSocket finds it open on first connect.
Write-Host "    waiting 8s for backend to bind 127.0.0.1:8100..."
Start-Sleep -Seconds 8

Write-Host "`n==> Starting Tauri dev (frontend will hot-reload, edit src/ to see changes)" -ForegroundColor Cyan
Push-Location $tauri
try {
    npm run tauri dev
} finally {
    Pop-Location
}
