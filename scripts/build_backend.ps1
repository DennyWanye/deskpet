# P3-S4 — Build the frozen backend.
#
# Produces `backend/dist/deskpet-backend/deskpet-backend.exe` that
# Rust `backend_launch::resolve`'s Bundled branch can spawn directly
# on the end user's machine without any Python installed.
#
# Usage (from repo root):
#   powershell scripts/build_backend.ps1
#
# Requires `backend/.venv/` with `pyinstaller` + `pyinstaller-hooks-contrib`
# installed (see `backend/pyproject.toml` `[project.optional-dependencies].dev`).

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot "backend"
$pyExe = Join-Path $backendDir ".venv\Scripts\python.exe"

if (-not (Test-Path $pyExe)) {
    Write-Error "Python venv not found at $pyExe — run 'python -m venv backend/.venv' first."
}

# Clean previous build so leftover artefacts don't mask fresh failures.
$distDir = Join-Path $backendDir "dist"
$buildDir = Join-Path $backendDir "build"
Remove-Item -Recurse -Force $distDir, $buildDir -ErrorAction SilentlyContinue

Push-Location $backendDir
try {
    Write-Host "[build_backend] running PyInstaller..."
    $t0 = Get-Date
    & $pyExe -m PyInstaller deskpet-backend.spec --noconfirm --clean
    if ($LASTEXITCODE -ne 0) {
        Write-Error "PyInstaller failed with exit code $LASTEXITCODE"
    }
    $elapsed = ((Get-Date) - $t0).TotalSeconds
    Write-Host ("[build_backend] build time: {0:N1}s" -f $elapsed)
}
finally {
    Pop-Location
}

# Size report — we want to know when this drifts past the P3-G2 budget.
$out = Join-Path $distDir "deskpet-backend"
$exe = Join-Path $out "deskpet-backend.exe"
if (-not (Test-Path $exe)) {
    Write-Error "Expected $exe not produced. Check PyInstaller output."
}

$bytes = (Get-ChildItem $out -Recurse -File | Measure-Object -Property Length -Sum).Sum
$mb = [math]::Round($bytes / 1MB, 1)
Write-Host ""
Write-Host "=========================================="
Write-Host "  frozen backend: $exe"
Write-Host ("  total size:     {0} MB" -f $mb)
Write-Host "=========================================="
Write-Host ""
Write-Host "Smoke test with:  python scripts/smoke_frozen_backend.py"
