# Additive feature-flag backfill — GUI real-test launcher.
#
# Boots a CLEAN DeskPet instance on ISOLATED ports (so it never disturbs an
# already-running pet on 8100/5173) with:
#   * SOURCE backend (DESKPET_BACKEND_DIR + DESKPET_PYTHON) → runs THIS
#     checkout's config.py (my additive-merge change), NOT the stale frozen exe.
#   * ISOLATED userdata dir pre-seeded with a STALE config.toml (missing
#     curation_nudge) — the 存量-install fixture the migration must upgrade.
#   * NO DESKPET_CONFIG — so resolve_config_path() takes the seed/merge path
#     against the userdata config (the code under test). Setting DESKPET_CONFIG
#     would short-circuit that and invalidate the test.
#
# Logs tauri dev output (incl. backend stderr per pitfall #7) to a file.
param(
    [int]$BackendPort = 8150,
    [int]$VitePort = 5190
)
$ErrorActionPreference = "Stop"

$root    = "/path/to/deskpet"
$backend = Join-Path $root "backend"
$venvPy  = Join-Path $backend ".venv\Scripts\python.exe"
$outDir  = Join-Path $root "plans\manual-results-2026-06-23-backfill"
$dataDir = Join-Path $outDir "userdata"
$ssDir   = Join-Path $outDir "screenshots"
$log     = Join-Path $outDir "tauri-dev.log"

New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
New-Item -ItemType Directory -Force -Path $ssDir | Out-Null

# 1) Pre-seed the STALE 存量 config via Python (UTF-8 safe; PowerShell would
#    mojibake the Chinese comments).
& $venvPy (Join-Path $backend "scripts\prep_stale_userdata_config.py") $dataDir
if ($LASTEXITCODE -ne 0) { throw "stale-config prep failed" }

# 2) Free ONLY my isolated ports (never the other instance's 8100/5173).
foreach ($port in @($BackendPort, $VitePort)) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object {
            Write-Host "  freeing port $port (PID $($_.OwningProcess))"
            Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
        }
}

# 3) Env: isolated ports + userdata, source backend, dev mode. NO DESKPET_CONFIG.
$env:DESKPET_BACKEND_PORT = "$BackendPort"
$env:DESKPET_VITE_PORT    = "$VitePort"
$env:DESKPET_USER_DATA_DIR = $dataDir
$env:DESKPET_BACKEND_DIR  = $backend
$env:DESKPET_PYTHON       = $venvPy
$env:DESKPET_DEV_MODE     = "1"
Remove-Item Env:\DESKPET_CONFIG -ErrorAction SilentlyContinue

Set-Location (Join-Path $root "tauri-app")

# devUrl patch via temp file (PowerShell mangles inline --config JSON).
$cfgFile = Join-Path $env:TEMP "deskpet-backfill-devurl-$VitePort.json"
Set-Content -Path $cfgFile -Encoding ascii -Value (
    '{"build":{"devUrl":"http://localhost:' + $VitePort + '"}}'
)

"=== backfill GUI verify: backend=$backend userdata=$dataDir ports=$BackendPort/$VitePort ===" | Out-File -FilePath $log -Encoding utf8
# Stream output via cmd redirection (PowerShell `*>>` buffers native-process
# output until the pipeline completes — backend structlog never appears live;
# cmd `> log 2>&1` writes as the backend emits, so the log is readable during
# the run and survives a force-kill of the children).
try {
    & cmd /c "npx tauri dev --config `"$cfgFile`" >> `"$log`" 2>&1"
} finally {
    Remove-Item $cfgFile -ErrorAction SilentlyContinue
}
