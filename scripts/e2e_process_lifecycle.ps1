# WI-05 (beta-100) — process lifecycle verification.
#
# Verifies the manual-test "路径 C" assertions without a human babysitting
# the task manager: there must be ZERO orphan deskpet / python processes
# after the app exits, and no state.db.bak.* explosion.
#
# Usage:
#   # one-shot residue check (run AFTER you've quit DeskPet manually):
#   powershell -File scripts/e2e_process_lifecycle.ps1 -CheckOnly
#
#   # full automated loop (requires -LaunchCmd that starts DeskPet and
#   # returns once it's up; and the app must honour a clean quit):
#   powershell -File scripts/e2e_process_lifecycle.ps1 `
#       -LaunchCmd "npm --prefix tauri-app run tauri dev" -Loops 10
#
# Exit code 0 = all checks passed, 1 = a residue / backup-explosion found.

param(
    [int]$Loops = 10,
    [string]$LaunchCmd = "",
    [switch]$CheckOnly,
    [int]$SettleSeconds = 5
)

$ErrorActionPreference = "Stop"

# Process names we consider "DeskPet residue". `python` is broad — we
# additionally filter by command line containing 'deskpet' / 'main.py'.
$DeskpetProcNames = @("deskpet", "DeskPet")

function Get-DeskpetResidue {
    $hits = @()
    foreach ($n in $DeskpetProcNames) {
        $hits += Get-Process -Name $n -ErrorAction SilentlyContinue
    }
    # Python backend: match on command line, not just the bare exe name,
    # so we don't flag the user's unrelated Python processes.
    $pythons = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue
    foreach ($p in $pythons) {
        if ($p.CommandLine -and ($p.CommandLine -match "deskpet" -or $p.CommandLine -match "main\.py")) {
            $hits += $p
        }
    }
    return $hits
}

function Test-BackupExplosion {
    # WI-05 / known regression: 5-21 fixed runaway state.db.bak.* files.
    # MAX_BACKUPS is 3 (see backend/deskpet/memory/migrator.py).
    $dataDir = Join-Path $env:APPDATA "deskpet\data"
    if (-not (Test-Path $dataDir)) {
        # portable mode — try <repo>/backend/userdata/data
        $dataDir = Join-Path $PSScriptRoot "..\backend\userdata\data"
    }
    if (-not (Test-Path $dataDir)) {
        Write-Host "  (data dir not found — skipping backup check)" -ForegroundColor DarkGray
        return $true
    }
    $baks = Get-ChildItem -Path $dataDir -Filter "state.db.bak.*" -ErrorAction SilentlyContinue
    $count = ($baks | Measure-Object).Count
    Write-Host "  state.db.bak.* count: $count (cap = 3)"
    return ($count -le 3)
}

function Invoke-ResidueCheck {
    param([string]$Label)
    $residue = Get-DeskpetResidue
    $n = ($residue | Measure-Object).Count
    if ($n -eq 0) {
        Write-Host "  [$Label] OK — 0 residual processes" -ForegroundColor Green
        return $true
    } else {
        Write-Host "  [$Label] FAIL — $n residual process(es):" -ForegroundColor Red
        foreach ($r in $residue) {
            $rid = if ($r.PSObject.Properties['ProcessId']) { $r.ProcessId } else { $r.Id }
            $rname = if ($r.PSObject.Properties['Name']) { $r.Name } else { $r.ProcessName }
            Write-Host "    PID $rid  $rname" -ForegroundColor Red
        }
        return $false
    }
}

# ----------------------------------------------------------------------

Write-Host "=== WI-05 process lifecycle verification ===" -ForegroundColor Cyan

if ($CheckOnly) {
    Write-Host "`n[CheckOnly] verifying no residue right now..."
    $ok = Invoke-ResidueCheck -Label "now"
    $bakOk = Test-BackupExplosion
    if ($ok -and $bakOk) {
        Write-Host "`nPASS" -ForegroundColor Green
        exit 0
    } else {
        Write-Host "`nFAIL" -ForegroundColor Red
        exit 1
    }
}

if (-not $LaunchCmd) {
    Write-Host "No -LaunchCmd supplied. Either pass one for the automated" -ForegroundColor Yellow
    Write-Host "loop, or use -CheckOnly after quitting DeskPet manually." -ForegroundColor Yellow
    Write-Host "`nFalling back to a single CheckOnly pass:" -ForegroundColor Yellow
    $ok = Invoke-ResidueCheck -Label "now"
    Test-BackupExplosion | Out-Null
    if ($ok) { exit 0 } else { exit 1 }
}

$allOk = $true
for ($i = 1; $i -le $Loops; $i++) {
    Write-Host "`n--- Loop $i / $Loops ---"
    Write-Host "  launching: $LaunchCmd"
    # NOTE: the launch command is expected to start DeskPet detached.
    # The operator (or windows-mcp test agent) drives the quit; this
    # script just verifies the post-quit state. For a fully hands-off
    # loop the LaunchCmd must itself start + auto-quit the app.
    Start-Process -FilePath "powershell" -ArgumentList "-NoProfile","-Command",$LaunchCmd -WindowStyle Hidden | Out-Null
    Start-Sleep -Seconds $SettleSeconds
    # Operator-driven quit happens here in manual mode.
    Start-Sleep -Seconds $SettleSeconds
    if (-not (Invoke-ResidueCheck -Label "loop $i")) { $allOk = $false }
}

Write-Host "`n=== backup-explosion check ==="
if (-not (Test-BackupExplosion)) { $allOk = $false }

if ($allOk) {
    Write-Host "`nPASS — all $Loops loops clean, no backup explosion" -ForegroundColor Green
    exit 0
} else {
    Write-Host "`nFAIL — see residue above" -ForegroundColor Red
    exit 1
}
