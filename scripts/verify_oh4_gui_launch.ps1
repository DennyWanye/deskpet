# WI-OH-4 GUI real-test launcher (single clean instance, default ports).
# SOURCE backend (DESKPET_BACKEND_DIR) + FLAGGED repo config (DESKPET_CONFIG) +
# default AppData userdata (reuse onboarding/keychain → no re-login). Logs the
# tauri dev output (incl. backend stderr per pitfall #7) to a file.
$ErrorActionPreference = "Stop"

$root    = "/path/to/deskpet"
$backend = Join-Path $root "backend"
$venvPy  = Join-Path $backend ".venv\Scripts\python.exe"
$logDir  = Join-Path $root "plans\manual-results-2026-06-23"
$log     = Join-Path $logDir "tauri-dev.log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $logDir "screenshots") | Out-Null

$env:DESKPET_BACKEND_DIR = $backend                       # run-from-source
$env:DESKPET_PYTHON      = $venvPy
$env:DESKPET_DEV_MODE    = "1"
$env:DESKPET_CONFIG      = Join-Path $root "config.toml"   # FLAGGED config (curation_nudge=true)

Set-Location (Join-Path $root "tauri-app")
"=== OH4 GUI verify launch: source backend=$backend config=$($env:DESKPET_CONFIG) ===" | Out-File -FilePath $log -Encoding utf8
& npx tauri dev *>> $log
