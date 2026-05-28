$ErrorActionPreference = "Stop"
$repo = "G:\projects\deskpet"
$backend = "$repo\backend"
$tauri = "$repo\tauri-app"
$userdata = "$backend\userdata"

New-Item -ItemType Directory -Force -Path $userdata | Out-Null

Get-Process | Where-Object { $_.ProcessName -in @("deskpet","deskpet-backend") } |
    Stop-Process -Force -ErrorAction SilentlyContinue

$env:DESKPET_USER_DATA_DIR = $userdata
$env:DESKPET_DEV_MODE      = "1"
$env:DESKPET_PYTHON        = "$backend\.venv\Scripts\python.exe"
$env:DESKPET_BACKEND_DIR   = $backend

Set-Location $tauri
npm run tauri dev
