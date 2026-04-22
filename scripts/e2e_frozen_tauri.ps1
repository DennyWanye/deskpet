# P3-S5 -- Tauri -> frozen backend end-to-end smoke.
#
# Preconditions (script self-checks, errors early if missing):
#   1. `powershell scripts/build_backend.ps1` was run;
#      backend/dist/deskpet-backend/deskpet-backend.exe exists.
#   2. backend/models/ contains faster-whisper-large-v3-turbo/ and cosyvoice2/.
#      (Before P3-S6 lands, models stay outside the bundle and are located
#      at runtime via DESKPET_MODEL_ROOT env var, set by this script.)
#   3. tauri-app/node_modules/ is installed.
#
# What it does:
#   * Kills stale deskpet.exe / deskpet-backend.exe only -- does NOT kill
#     node/vite to avoid interfering with parallel dev workstreams.
#   * Starts `npm run tauri dev` in the background (stdout -> .claude/tauri_dev.log).
#   * Polls http://127.0.0.1:8100/health for up to 90 seconds.
#   * Asserts status == "ok" and startup_errors is empty.
#   * Greps the dev log for "[backend_launch] Bundled exe=" to confirm the
#     Bundled branch was taken (not the Dev Python fallback).
#   * Leaves the Tauri window running so the user can do the UI-level E2E
#     (mic test, ASR, LLM reply, TTS playback) and screenshot.
#
# Exit codes:
#   0 -- /health ok and Tauri still running
#   1 -- any step failed
#   2 -- missing precondition

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

$repoRoot  = Split-Path -Parent $PSScriptRoot
$frozenExe = Join-Path $repoRoot "backend\dist\deskpet-backend\deskpet-backend.exe"
$modelRoot = Join-Path $repoRoot "backend\models"
$tauriDir  = Join-Path $repoRoot "tauri-app"

# P3-S5 hotfix: frozen backend's _REPO_FFMPEG path resolves to
# <exe_dir>/backend/bin/ffmpeg.exe (via __file__.parents[2]), which doesn't
# exist in the bundle. Point it at the shared portable ffmpeg in the main
# repo (setup_ffmpeg.ps1 drops it at backend/bin/ffmpeg.exe). If missing,
# fall back to PATH.
$ffmpegMain = "G:\projects\deskpet\backend\bin\ffmpeg.exe"
$ffmpegWt   = Join-Path $repoRoot "backend\bin\ffmpeg.exe"
if (Test-Path $ffmpegWt) {
    $env:DESKPET_FFMPEG = $ffmpegWt
} elseif (Test-Path $ffmpegMain) {
    $env:DESKPET_FFMPEG = $ffmpegMain
}

# --- 1. Preconditions --------------------------------------------------
if (-not (Test-Path $frozenExe)) {
    Write-Host "[e2e] MISSING: $frozenExe" -ForegroundColor Red
    Write-Host "[e2e] Run first: powershell scripts/build_backend.ps1"
    exit 2
}
if (-not (Test-Path $modelRoot)) {
    Write-Host "[e2e] MISSING: $modelRoot" -ForegroundColor Red
    Write-Host "[e2e] Place faster-whisper-large-v3-turbo/ + cosyvoice2/ under backend/models/"
    exit 2
}
if (-not (Test-Path (Join-Path $tauriDir "node_modules"))) {
    Write-Host "[e2e] MISSING: tauri-app/node_modules -- run npm install in tauri-app/ first" -ForegroundColor Red
    exit 2
}

# --- 2. Kill stale deskpet processes (conservative) -------------------
Write-Host "[e2e] cleaning stale deskpet / deskpet-backend processes..."
Get-Process -Name "deskpet", "deskpet-backend" -ErrorAction SilentlyContinue |
    ForEach-Object {
        Write-Host "  kill $($_.Name) PID=$($_.Id)"
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }

# --- 3. Start tauri dev in background ---------------------------------
$env:DESKPET_MODEL_ROOT = $modelRoot
Write-Host "[e2e] DESKPET_MODEL_ROOT=$modelRoot"
$configToml = Join-Path $repoRoot "config.toml"
if (Test-Path $configToml) {
    $env:DESKPET_CONFIG = $configToml
    Write-Host "[e2e] DESKPET_CONFIG=$configToml"
}
if ($env:DESKPET_FFMPEG) {
    Write-Host "[e2e] DESKPET_FFMPEG=$env:DESKPET_FFMPEG"
} else {
    Write-Host "[e2e] WARN: ffmpeg not found; TTS will fail. Run scripts/setup_ffmpeg.ps1" -ForegroundColor Yellow
}
Write-Host "[e2e] starting: npm run tauri dev (background)"

$logDir = Join-Path $repoRoot ".claude"
$null = New-Item -ItemType Directory -Force -Path $logDir -ErrorAction SilentlyContinue
$logPath = Join-Path $logDir "tauri_dev.log"
$errPath = "$logPath.err"

Push-Location $tauriDir
try {
    $tauriProc = Start-Process -FilePath "npm.cmd" `
        -ArgumentList @("run", "tauri", "dev") `
        -PassThru `
        -NoNewWindow `
        -RedirectStandardOutput $logPath `
        -RedirectStandardError  $errPath
} finally {
    Pop-Location
}

Write-Host "[e2e] tauri dev PID=$($tauriProc.Id), log: $logPath"

# --- 4. Poll /health, max 90s (covers resources copy + cold boot) -----
$healthUrl = "http://127.0.0.1:8100/health"
$deadline  = (Get-Date).AddSeconds(90)
$healthBody = $null

Write-Host "[e2e] waiting for /health (max 90s)..."
while ((Get-Date) -lt $deadline) {
    if ($tauriProc.HasExited) {
        Write-Host "[e2e] FAIL: tauri dev exited rc=$($tauriProc.ExitCode)" -ForegroundColor Red
        Write-Host "[e2e] last 30 stdout lines:"
        Get-Content $logPath -Tail 30 -ErrorAction SilentlyContinue
        Write-Host "[e2e] last 30 stderr lines:"
        Get-Content $errPath -Tail 30 -ErrorAction SilentlyContinue
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
    Write-Host "[e2e] FAIL: /health did not respond within 90s" -ForegroundColor Red
    Write-Host "[e2e] last 30 stdout lines:"
    Get-Content $logPath -Tail 30 -ErrorAction SilentlyContinue
    Write-Host "[e2e] last 30 stderr lines:"
    Get-Content $errPath -Tail 30 -ErrorAction SilentlyContinue
    exit 1
}

# --- 5. Assertions ----------------------------------------------------
Write-Host "[e2e] /health = $($healthBody | ConvertTo-Json -Compress)"
if ($healthBody.status -ne "ok") {
    Write-Host "[e2e] FAIL: status != ok" -ForegroundColor Red
    exit 1
}
if ($healthBody.startup_errors -and $healthBody.startup_errors.Count -gt 0) {
    Write-Host "[e2e] FAIL: startup_errors non-empty: $($healthBody.startup_errors)" -ForegroundColor Red
    exit 1
}

# --- 6. Confirm Bundled branch via log grep ---------------------------
$allLogs = @()
if (Test-Path $logPath) { $allLogs += Get-Content $logPath }
if (Test-Path $errPath) { $allLogs += Get-Content $errPath }
$bundled = $allLogs | Select-String -Pattern "\[backend_launch\]\s+Bundled"
if ($bundled) {
    Write-Host "[e2e] OK: Bundled branch confirmed in log:" -ForegroundColor Green
    Write-Host "       $($bundled[0].Line)"
} else {
    $devLine = $allLogs | Select-String -Pattern "\[backend_launch\]\s+Dev"
    if ($devLine) {
        Write-Host "[e2e] WARN: took Dev branch instead of Bundled:" -ForegroundColor Yellow
        Write-Host "       $($devLine[0].Line)"
        Write-Host "       Bundle resources may not have been copied to target/debug/."
    } else {
        Write-Host "[e2e] WARN: no [backend_launch] log line found yet" -ForegroundColor Yellow
        Write-Host "       Check $logPath manually once tauri dev settles."
    }
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host "  [e2e] PASS -- /health ok, backend preloaded" -ForegroundColor Green
Write-Host "  tauri dev still running (PID=$($tauriProc.Id))" -ForegroundColor Green
Write-Host "  Now do the UI-level E2E:" -ForegroundColor Green
Write-Host "    1. window appears" -ForegroundColor Green
Write-Host "    2. click mic, say something (e.g. 'hello')" -ForegroundColor Green
Write-Host "    3. verify ASR + AI reply + TTS playback" -ForegroundColor Green
Write-Host "    4. screenshot the window" -ForegroundColor Green
Write-Host "  When done, press Ctrl+C in the tauri dev console to stop." -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
exit 0
