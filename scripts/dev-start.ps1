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

# 2026-05-15: 必须显式指向 .venv python，否则 PATH 会解析到系统 Python，
# 进而装在 .venv 的 FlagEmbedding / torch-cuda 全部白装，BGE-M3 静默
# 降级 mock。dev 模式 venv 是 backend/.venv，frozen 模式由 backend_launch.rs
# 解析；这里只管 dev。
$venvPy = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "ERROR: backend .venv missing at $venvPy" -ForegroundColor Red
    Write-Host "  Run: cd backend; python -m venv .venv; .venv\Scripts\Activate.ps1; pip install -e .[dev]" -ForegroundColor Yellow
    exit 1
}

Write-Host "==> Starting backend in a new console window..." -ForegroundColor Cyan
Write-Host "    interpreter: $venvPy" -ForegroundColor DarkGray
$env_args = @{
    DESKPET_USER_DATA_DIR = $userdata
    DESKPET_DEV_MODE      = "1"
    # Make Tauri's backend_launch.rs use the same .venv if it ever spawns
    # a parallel sidecar (defends against the "two backends, one wins
    # 8100" race we hit on 2026-05-15).
    DESKPET_PYTHON        = $venvPy
}
$envCmd = ($env_args.GetEnumerator() | ForEach-Object { "`$env:$($_.Key)='$($_.Value)'" }) -join "; "
Start-Process -FilePath "powershell" `
    -ArgumentList "-NoExit", "-Command", "$envCmd; cd '$backend'; & '$venvPy' main.py" `
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
