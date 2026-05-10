# Build a Windows MSI installer for DeskPet, end-to-end.
#
# Why this script exists
# ----------------------
# Tauri's bundled WiX template emits a single <Media Id="1" Cabinet="app.cab"
# EmbedCab="yes"/> in the generated main.wxs. That cab format has a hard
# 2 GiB-per-cab limit. Our PyInstaller dist (faster-whisper INT8 + BGE-M3
# fp16 + full torch CUDA stack) is ~7.7 GiB and trips the limit; light.exe
# crashes inside CreateCabFinish with E_UNEXPECTED.
#
# Fix: rewrite that one line to <MediaTemplate MaximumUncompressedMediaSize=
# "1900" .../>, which makes WiX shard the payload across multiple cabs
# automatically. Each cab is then under the 2 GiB ceiling.
#
# Tauri 2.10's bundler does not expose a hook for swapping the Media element,
# so we run `tauri build` (which fails at the light.exe step), hot-patch the
# generated wxs, and re-run candle+light by hand.
#
# Usage
# -----
#   pwsh -File scripts/build-msi.ps1
#
# Prerequisites
# -------------
#   * backend/dist-msi/deskpet-backend/   (PyInstaller output, ~7.7 GiB)
#   * Tauri tooling: WixTools314 in %LOCALAPPDATA%/tauri/
#   * Windows 64-bit (32-bit makensis can't handle this size)
#
# Output
# ------
#   tauri-app/src-tauri/target/release/bundle/msi/DeskPet_*.msi (~5.4 GiB)

$ErrorActionPreference = "Stop"

# Use G: for build temp — C: was running 99% full from the cu124 torch cache
# during dev. Keep wix scratch off the system drive so makensis/light don't
# trip ERROR_DISK_FULL halfway through.
$env:TEMP = "G:\temp\wix-tmp"
$env:TMP  = "G:\temp\wix-tmp"
$env:CARGO_TARGET_DIR = "G:\temp\cargo-target"
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null

$repo  = (Resolve-Path "$PSScriptRoot\..").Path
$tauri = Join-Path $repo "tauri-app"
$wixDir = Join-Path $env:CARGO_TARGET_DIR "release\wix\x64"
$wixToolsBin = "$env:LOCALAPPDATA\tauri\WixTools314"

Write-Host "==> Step 1/4: tauri build (will fail at light.exe — that is expected)"
Push-Location $tauri
try {
    npm run tauri -- build 2>&1 | Out-Host
}
catch {
    # Swallow the expected light.exe failure.
}
Pop-Location

if (-not (Test-Path "$wixDir\main.wxs")) {
    throw "main.wxs missing at $wixDir; the candle step never ran."
}

Write-Host "==> Step 2/4: patch <Media> -> <MediaTemplate> (multi-cab + external)"
# Two-part patch:
#   1. <Media> -> <MediaTemplate>: shards the 7.7 GB payload across cabs
#      < 1.9 GB each (single cab has a 2 GB limit).
#   2. EmbedCab="no": writes the cabs as siblings of the .msi instead of
#      embedded inside it (P4-S21 #2). Result: Windows Installer cache
#      (C:\Windows\Installer\) only stores the small .msi (~10 MB)
#      instead of the full 5.4 GB payload, so install no longer needs
#      ~7 GB free on C: drive. Trade-off: distribute as a folder
#      (.msi + N .cab files) — see scripts/build-msi-readme.md.
$wxsPath = Join-Path $wixDir "main.wxs"
$content = Get-Content $wxsPath -Raw
$patched = $content -replace `
    '<Media Id="1" Cabinet="app\.cab" EmbedCab="yes" />', `
    '<MediaTemplate EmbedCab="no" CompressionLevel="mszip" MaximumUncompressedMediaSize="1900" />'

if ($patched -eq $content) {
    Write-Warning "Media element not found — wxs already patched, or template has changed?"
} else {
    Set-Content -LiteralPath $wxsPath -Value $patched -Encoding utf8
    Write-Host "    patched ok (multi-cab + external)"
}

Write-Host "==> Step 3/4: re-run candle"
Push-Location $wixDir
& "$wixToolsBin\candle.exe" -arch x64 -ext WixUIExtension -ext WixUtilExtension main.wxs | Out-Host
if ($LASTEXITCODE -ne 0) {
    Pop-Location
    throw "candle failed with exit code $LASTEXITCODE"
}

Write-Host "==> Step 4/4: re-run light (multi-cab; takes ~3 minutes for 7.7 GiB)"
& "$wixToolsBin\light.exe" `
    -loc locale.wxl `
    -ext WixUIExtension `
    -ext WixUtilExtension `
    -cultures:en-US `
    main.wixobj `
    -out DeskPet_x64.msi | Out-Host
$lightRc = $LASTEXITCODE
Pop-Location
if ($lightRc -ne 0) {
    throw "light failed with exit code $lightRc"
}

Write-Host "==> Done. Copying MSI + sibling .cab files to bundle/msi/"
$bundleDir = Join-Path $tauri "src-tauri\target\release\bundle\msi"
# Wipe stale .msi/.cab from a previous build so we don't ship a mix.
if (Test-Path $bundleDir) {
    Get-ChildItem $bundleDir -Filter "*.cab" | Remove-Item -Force -ErrorAction SilentlyContinue
    Get-ChildItem $bundleDir -Filter "*.msi" | Remove-Item -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force -Path $bundleDir | Out-Null

# Try to read the version from tauri.conf.json so the output filename matches
# what Tauri would have produced on its own.
$tauriConf = Get-Content (Join-Path $tauri "src-tauri\tauri.conf.json") -Raw | ConvertFrom-Json
$version = $tauriConf.version
$dstName = "DeskPet_${version}_x64_en-US.msi"
$dstPath = Join-Path $bundleDir $dstName
Copy-Item -LiteralPath (Join-Path $wixDir "DeskPet_x64.msi") -Destination $dstPath -Force

# External cabs: light.exe writes them as siblings of the .msi in $wixDir.
# Copy them all to $bundleDir so users can ship one folder.
$cabs = Get-ChildItem $wixDir -Filter "*.cab"
foreach ($cab in $cabs) {
    Copy-Item -LiteralPath $cab.FullName -Destination $bundleDir -Force
}

$msiSize = (Get-Item $dstPath).Length / 1MB
$cabsSize = if ($cabs.Count -gt 0) {
    ($cabs | Measure-Object -Property Length -Sum).Sum / 1GB
} else { 0.0 }
Write-Host ("    msi : {0}  ({1:N1} MB)" -f $dstPath, $msiSize)
Write-Host ("    cabs: {0} files, {1:N2} GiB total" -f $cabs.Count, $cabsSize)
Write-Host ""
Write-Host "External cab layout (P4-S21 #2):"
Write-Host "  * Windows Installer cache only stores the .msi (~10 MB) on C:,"
Write-Host "    not the cabs — install no longer needs ~7 GB free on C: drive."
Write-Host "  * Distribute the WHOLE folder ($bundleDir) — both .msi AND"
Write-Host "    sibling .cab files. Just the .msi alone won't install."
Write-Host ""
Write-Host "To install: double-click the .msi inside the folder, or"
Write-Host "  msiexec /i `"$dstPath`""
