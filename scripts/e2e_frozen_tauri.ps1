# P3-S5 — Tauri → 冻结 backend 端到端 smoke
#
# 先决条件（脚本会自检并在缺条件时提示）：
#   1. `powershell scripts/build_backend.ps1` 已跑过，
#      `backend/dist/deskpet-backend/deskpet-backend.exe` 存在
#   2. `backend/models/` 下有 faster-whisper-large-v3-turbo/ 和 cosyvoice2/
#      （P3-S6 之前模型不进 bundle，靠 DESKPET_MODEL_ROOT 临时指过去）
#   3. `tauri-app/node_modules/` 已装
#
# 流程：
#   * 清理老的 orphan deskpet.exe + deskpet-backend.exe（不动 node / vite，
#     避免误伤其他并行开发流）
#   * 启动 `npm run tauri dev`（后台，设 DESKPET_MODEL_ROOT）
#   * 轮询 http://127.0.0.1:8100/health 最多 90 秒
#   * 断言 status=ok，startup_errors 空
#   * PASS 之后**不自动 kill Tauri**——留给用户做 UI 层 E2E（点麦讲话 + 截图）
#
# 退出码：
#   0 — /health ok，Tauri 实例还在跑（用户自己 Ctrl+C）
#   1 — 任一步失败
#   2 — 前置条件缺失
#
# Usage:
#   powershell scripts/e2e_frozen_tauri.ps1

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

$repoRoot   = Split-Path -Parent $PSScriptRoot
$frozenExe  = Join-Path $repoRoot "backend\dist\deskpet-backend\deskpet-backend.exe"
$modelRoot  = Join-Path $repoRoot "backend\models"
$tauriDir   = Join-Path $repoRoot "tauri-app"

# --- 1. 前置检查 --------------------------------------------------------
if (-not (Test-Path $frozenExe)) {
    Write-Host "[e2e] 缺: $frozenExe" -ForegroundColor Red
    Write-Host "[e2e] 先跑: powershell scripts/build_backend.ps1"
    exit 2
}
if (-not (Test-Path $modelRoot)) {
    Write-Host "[e2e] 缺: $modelRoot" -ForegroundColor Red
    Write-Host "[e2e] 放 faster-whisper-large-v3-turbo/ + cosyvoice2/ 在 backend/models/ 下"
    exit 2
}
if (-not (Test-Path (Join-Path $tauriDir "node_modules"))) {
    Write-Host "[e2e] 缺: tauri-app/node_modules — 先 cd tauri-app && npm install" -ForegroundColor Red
    exit 2
}

# --- 2. 清理 deskpet 进程（保守：只碰 deskpet 相关） -------------------
Write-Host "[e2e] 清理旧的 deskpet / deskpet-backend 进程..."
Get-Process -Name "deskpet", "deskpet-backend" -ErrorAction SilentlyContinue |
    ForEach-Object {
        Write-Host "  kill $($_.Name) PID=$($_.Id)"
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }

# --- 3. 启动 tauri dev（后台） -----------------------------------------
$env:DESKPET_MODEL_ROOT = $modelRoot
Write-Host "[e2e] DESKPET_MODEL_ROOT=$modelRoot"
Write-Host "[e2e] 启动 npm run tauri dev（后台）..."

$logPath = Join-Path $repoRoot ".claude\tauri_dev.log"
$null = New-Item -ItemType Directory -Force -Path (Split-Path $logPath -Parent) -ErrorAction SilentlyContinue

Push-Location $tauriDir
try {
    $tauriProc = Start-Process -FilePath "npm.cmd" `
        -ArgumentList @("run", "tauri", "dev") `
        -PassThru `
        -NoNewWindow `
        -RedirectStandardOutput $logPath `
        -RedirectStandardError "$logPath.err"
} finally {
    Pop-Location
}

Write-Host "[e2e] tauri dev PID=$($tauriProc.Id)，日志: $logPath"

# --- 4. 轮询 /health 最多 90s（首跑含 resources copy + backend 冷启动） -
$healthUrl = "http://127.0.0.1:8100/health"
$deadline = (Get-Date).AddSeconds(90)
$healthBody = $null

Write-Host "[e2e] 等 /health 就绪（上限 90s）..."
while ((Get-Date) -lt $deadline) {
    if ($tauriProc.HasExited) {
        Write-Host "[e2e] FAIL: tauri dev 进程已退出 rc=$($tauriProc.ExitCode)" -ForegroundColor Red
        Write-Host "[e2e] 最后 30 行日志:"
        Get-Content $logPath -Tail 30 -ErrorAction SilentlyContinue
        exit 1
    }
    try {
        $resp = Invoke-WebRequest -Uri $healthUrl -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
        $healthBody = $resp.Content | ConvertFrom-Json
        break
    } catch {
        Start-Sleep -Seconds 2
    }
}

if ($null -eq $healthBody) {
    Write-Host "[e2e] FAIL: 90s 内 /health 没响应" -ForegroundColor Red
    Write-Host "[e2e] 最后 30 行日志:"
    Get-Content $logPath -Tail 30 -ErrorAction SilentlyContinue
    exit 1
}

# --- 5. 断言 ----------------------------------------------------------
Write-Host "[e2e] /health = $($healthBody | ConvertTo-Json -Compress)"
if ($healthBody.status -ne "ok") {
    Write-Host "[e2e] FAIL: status != ok" -ForegroundColor Red
    exit 1
}
if ($healthBody.startup_errors -and $healthBody.startup_errors.Count -gt 0) {
    Write-Host "[e2e] FAIL: startup_errors 非空: $($healthBody.startup_errors)" -ForegroundColor Red
    exit 1
}

# --- 6. 查日志确认走的是 Bundled 分支 ---------------------------------
$logs = Get-Content $logPath -ErrorAction SilentlyContinue
$bundled = $logs | Select-String -Pattern "backend_launch.*Bundled|Bundled.*exe="
if ($bundled) {
    Write-Host "[e2e] ✓ 日志确认 Bundled 分支: $($bundled[0].Line)" -ForegroundColor Green
} else {
    Write-Host "[e2e] WARN: 日志里没看到 Bundled 字样（可能走了 env fallback）" -ForegroundColor Yellow
    Write-Host "[e2e] 请手动在日志里确认是从 resource_dir 起的 exe"
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host "  [e2e] PASS — backend /health ok" -ForegroundColor Green
Write-Host "  Tauri 还在跑 (PID=$($tauriProc.Id))，现在做 UI 层 E2E:" -ForegroundColor Green
Write-Host "    1. 窗口出现" -ForegroundColor Green
Write-Host "    2. 点麦讲话" -ForegroundColor Green
Write-Host "    3. 看 ASR + AI 回复 + TTS 播放" -ForegroundColor Green
Write-Host "    4. 截图留证" -ForegroundColor Green
Write-Host "  完事 Ctrl+C 关闭 tauri dev" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
exit 0
