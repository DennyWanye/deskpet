# GUI chat test via the INSTALLED DeskPet shell (f:\deskpet\deskpet.exe) — which
# is what computer-use granted (the dev-built target\debug\deskpet.exe runs from a
# different path and gets masked). Point the installed shell at the SOURCE backend
# (DESKPET_BACKEND_DIR) + the already-migrated isolated userdata (curation_nudge=true),
# on an isolated port. Release shell loads bundled frontend → no vite needed.
# Captures backend stderr (inherited) to a log via cmd redirection.
param([int]$BackendPort = 8150)
$ErrorActionPreference = "Stop"

$root    = "/path/to/deskpet"
$backend = Join-Path $root "backend"
$venvPy  = Join-Path $backend ".venv\Scripts\python.exe"
$outDir  = Join-Path $root "plans\manual-results-2026-06-23-backfill"
$dataDir = Join-Path $outDir "userdata"
$log     = Join-Path $outDir "installed-shell.log"
$exe     = "F:\deskpet\deskpet.exe"

# free my isolated backend port
Get-NetTCPConnection -LocalPort $BackendPort -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }

$env:DESKPET_BACKEND_PORT  = "$BackendPort"
$env:DESKPET_USER_DATA_DIR = $dataDir
$env:DESKPET_BACKEND_DIR   = $backend
$env:DESKPET_PYTHON        = $venvPy
$env:DESKPET_DEV_MODE      = "1"
Remove-Item Env:\DESKPET_CONFIG    -ErrorAction SilentlyContinue
Remove-Item Env:\DESKPET_VITE_PORT -ErrorAction SilentlyContinue

"=== installed-shell + source backend: exe=$exe userdata=$dataDir port=$BackendPort ===" | Out-File -FilePath $log -Encoding utf8
& cmd /c "`"$exe`" >> `"$log`" 2>&1"
