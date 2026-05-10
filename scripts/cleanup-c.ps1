# Free space on C: drive — DeskPet MSI install needs ~7 GB free.
# Run in an ADMIN PowerShell:
#   Right-click Start → "Terminal (Admin)" → paste:
#   pwsh -File G:\projects\deskpet\scripts\cleanup-c.ps1
# (or `powershell -File ...` if you're on Windows PowerShell 5.1)

$ErrorActionPreference = "Continue"

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "ERROR: This script must run as Administrator." -ForegroundColor Red
    Write-Host "Right-click Start, choose 'Terminal (Admin)', then paste:"
    Write-Host "  pwsh -File $PSCommandPath"
    exit 1
}

function Show-Free {
    "{0:N2} GB free" -f ((Get-Volume C).SizeRemaining / 1GB)
}

Write-Host "=== Before ===" -ForegroundColor Cyan
Show-Free

# A1. Recycle Bin (~115 MB on your machine)
Write-Host "`n=== A1: Empty Recycle Bin ===" -ForegroundColor Cyan
try { Clear-RecycleBin -Force -ErrorAction Stop; Write-Host "  cleared" } catch { Write-Host "  $($_.Exception.Message)" }

# A2. Windows Update download cache (~96 MB)
Write-Host "`n=== A2: Windows Update download cache ===" -ForegroundColor Cyan
Stop-Service wuauserv -Force -ErrorAction SilentlyContinue
Stop-Service bits -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath 'C:\Windows\SoftwareDistribution\Download\*' -Recurse -Force -ErrorAction SilentlyContinue
Start-Service wuauserv -ErrorAction SilentlyContinue
Start-Service bits -ErrorAction SilentlyContinue
Write-Host "  done"

# A3. DISM component cleanup — clears WinSxS old versions (~1-3 GB)
Write-Host "`n=== A3: DISM component cleanup (5-10 min) ===" -ForegroundColor Cyan
& DISM.exe /Online /Cleanup-Image /StartComponentCleanup /ResetBase
Write-Host "  done"

# A4. Windows Defender old definitions
Write-Host "`n=== A4: Defender old signature cleanup ===" -ForegroundColor Cyan
$mp = "$env:ProgramData\Microsoft\Windows Defender\Platform"
if (Test-Path $mp) {
    Get-ChildItem $mp | Sort-Object Name -Descending | Select-Object -Skip 1 | ForEach-Object {
        Write-Host "  removing old defender platform: $($_.Name)"
        Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "`n=== After ===" -ForegroundColor Cyan
Show-Free
Write-Host ""
Write-Host "Next: open 'Settings → System → Storage → Temporary files'" -ForegroundColor Yellow
Write-Host "and tick 'Delivery Optimization Files' + 'Recycle Bin' + 'Temporary files' for another 1-3 GB."
