# P3-S10 Degraded Smoke (VirtualBox, no GPU passthrough)
# Covers T0/T1/T5/T6/T7/T8; skips T2 full/T3/T4 (GPU-gated).
# Run inside VM after installer copied to C:\DeskPetInstaller\

$ErrorActionPreference = 'Stop'
$results = [ordered]@{}
$installer_dir = "C:\DeskPetInstaller"
$nsis = Get-ChildItem $installer_dir -Filter "DeskPet_*-setup.exe" | Select-Object -First 1
$msi  = Get-ChildItem $installer_dir -Filter "DeskPet_*.msi"       | Select-Object -First 1

function Section($name) {
    Write-Host "`n===== $name =====" -ForegroundColor Cyan
}

function Record($k,$v) {
    $results[$k] = $v
    Write-Host "  $k : $v"
}

# ---- T0 baseline ----
Section "T0 baseline"
$os = Get-ComputerInfo | Select-Object WindowsProductName,OsBuildNumber,TotalPhysicalMemory
Record "OS"            $os.WindowsProductName
Record "Build"         $os.OsBuildNumber
Record "RAM GB"        ([math]::Round($os.TotalPhysicalMemory/1GB,1))
Record "AppData.deskpet exists (expect False)"      (Test-Path "$env:APPDATA\deskpet")
Record "LocalAppData.deskpet exists (expect False)" (Test-Path "$env:LOCALAPPDATA\deskpet")

# ---- T1 NSIS install ----
Section "T1 NSIS install"
if (-not $nsis) { Write-Host "  NSIS installer not found in $installer_dir" -ForegroundColor Red; exit 1 }
Record "installer file"    $nsis.Name
Record "installer size MB" ([math]::Round($nsis.Length/1MB,1))
$t0 = Get-Date
$p = Start-Process $nsis.FullName -ArgumentList '/S' -PassThru -Wait
$elapsed = ((Get-Date) - $t0).TotalSeconds
Record "install exit code"     $p.ExitCode
Record "install elapsed sec"   ([math]::Round($elapsed,1))
Record "gate <=60s (P3-G2)"    ($elapsed -le 60)

# Post-install layout
$pf = "$env:ProgramFiles\DeskPet"
Record "PF\DeskPet exists"        (Test-Path $pf)
Record "deskpet.exe exists"       (Test-Path "$pf\deskpet.exe")
Record "backend subdir exists"    (Test-Path "$pf\backend")
if (Test-Path $pf) {
    $sum = (Get-ChildItem $pf -Recurse -File | Measure-Object Length -Sum).Sum
    Record "install total MB"     ([math]::Round($sum/1MB,1))
}

# ---- T2 partial (file checks only, skip GPU-gated cold boot) ----
Section "T2 file checks (GPU-gated runtime checks SKIPPED)"
Record "NOTE" "T2 full cold-boot test SKIPPED - requires NVIDIA GPU passthrough"
# We won't launch the app; just verify installer seeding.
# First-launch seeds happen on run; we skip run. So AppData won't exist yet, this is expected.

# ---- T5 port conflict (SKIPPED in degraded: needs app to start) ----
Section "T5 port conflict red-splash (SKIPPED)"
Record "NOTE" "T5 SKIPPED - requires app launch which needs GPU"

# ---- T6 standard uninstall ----
Section "T6 standard uninstall"
# NSIS installer uninstaller path
$uninst = "$pf\Uninstall DeskPet.exe"
if (-not (Test-Path $uninst)) { $uninst = "$pf\uninstall.exe" }
if (-not (Test-Path $uninst)) {
    $uninst = Get-ChildItem $pf -Filter "uninstall*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1 | ForEach-Object FullName
}
Record "uninstaller path" $uninst
if ($uninst -and (Test-Path $uninst)) {
    $t0 = Get-Date
    $p = Start-Process $uninst -ArgumentList '/S' -PassThru -Wait
    Record "uninstall elapsed sec" ([math]::Round(((Get-Date) - $t0).TotalSeconds,1))
    Record "uninstall exit code"   $p.ExitCode
}
Start-Sleep 3
Record "PF\DeskPet gone (expect False)"             (Test-Path $pf)
Record "AppData\deskpet preserved (expect True/N-A on fresh)"  (Test-Path "$env:APPDATA\deskpet")
Record "LocalAppData\deskpet preserved"             (Test-Path "$env:LOCALAPPDATA\deskpet")

# ---- T8 MSI variant ----
Section "T8 MSI install + uninstall"
if ($msi) {
    Record "MSI file"    $msi.Name
    Record "MSI size MB" ([math]::Round($msi.Length/1MB,1))
    $t0 = Get-Date
    $p = Start-Process msiexec -ArgumentList @("/i","`"$($msi.FullName)`"","/quiet","/qn","/norestart") -PassThru -Wait
    Record "MSI install elapsed sec" ([math]::Round(((Get-Date) - $t0).TotalSeconds,1))
    Record "MSI install exit code"   $p.ExitCode
    Record "PF\DeskPet exists after MSI" (Test-Path $pf)
    if (Test-Path $pf) {
        $sum = (Get-ChildItem $pf -Recurse -File | Measure-Object Length -Sum).Sum
        Record "MSI install total MB"    ([math]::Round($sum/1MB,1))
    }
    # MSI uninstall
    $t0 = Get-Date
    $p2 = Start-Process msiexec -ArgumentList @("/x","`"$($msi.FullName)`"","/quiet","/qn","/norestart") -PassThru -Wait
    Record "MSI uninstall elapsed sec" ([math]::Round(((Get-Date) - $t0).TotalSeconds,1))
    Record "MSI uninstall exit code"   $p2.ExitCode
    Record "PF\DeskPet gone after MSI uninstall" (Test-Path $pf)
} else {
    Record "MSI file" "NOT FOUND - skipping T8"
}

# ---- Report ----
Section "Report"
$out = "C:\DeskPetInstaller\smoke_report.json"
$results | ConvertTo-Json -Depth 3 | Out-File $out -Encoding utf8
Write-Host "`nReport saved to: $out" -ForegroundColor Green
$results | Format-Table -AutoSize | Out-String | Write-Host
