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

# 2026-05-16 (companion-context-isolation Phase 4 dev-harness fix):
# 之前 dev-start.ps1 自己开一个 backend 控制台 + `npm run tauri dev` 里
# Tauri 的 backend_launch.rs 又开一个 → "两 backend 抢 8100" 竞态；且
# Tauri 进程没继承 DESKPET_BACKEND_DIR，backend_launch.rs Priority 1
# 被跳过，落到 stale 的 target/debug/backend/deskpet-backend.exe（旧
# PyInstaller 包），表现为 "找不到 Python backend / exited without
# SHARED_SECRET"，反复挡住所有 UI E2E。
#
# 修复：不再自己 spawn backend。把 4 个 env 设进**当前进程**，`npm run
# tauri dev` 子进程继承之 → backend_launch.rs Priority 1
# (DESKPET_BACKEND_DIR) 命中 → 用 .venv python 起**单一** backend。
# 单一拥有者，竞态消失，stale exe 不再被选中。
Write-Host "==> Configuring single-owner backend env (Tauri spawns it via .venv)" -ForegroundColor Cyan
Write-Host "    interpreter: $venvPy" -ForegroundColor DarkGray
$env:DESKPET_USER_DATA_DIR = $userdata
$env:DESKPET_DEV_MODE      = "1"
$env:DESKPET_PYTHON        = $venvPy
# Priority 1 in backend_launch.rs — the "I'm a dev, run from source"
# signal that must beat the bundled exe. THIS is the line whose absence
# caused the stale-exe bug.
$env:DESKPET_BACKEND_DIR   = $backend

Write-Host "`n==> Starting Tauri dev (single backend via .venv; frontend hot-reloads)" -ForegroundColor Cyan
Push-Location $tauri
try {
    npm run tauri dev
} finally {
    Pop-Location
}
