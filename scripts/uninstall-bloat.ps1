# Uninstall apps that user OK'd to remove during DeskPet MSI install troubleshoot.
# Run in an ADMIN PowerShell.
#
# B-level (user said yes to all except UU远程):
#   REDlauncher, Epic Online Services, .NET Core SDK 3.1.426,
#   Paradox Launcher v2, Apifox, Unity Hub, Xmind, 作家助手
# C-level (user said yes):
#   Docker Desktop, 飞书, Windows SDK x2

$ErrorActionPreference = "Continue"

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "ERROR: This script must run as Administrator." -ForegroundColor Red
    exit 1
}

function Show-Free { "{0:N2} GB free" -f ((Get-Volume C).SizeRemaining / 1GB) }

# Each entry is a substring matched against DisplayName in the Uninstall registry.
# We use the registry's UninstallString rather than winget so that apps installed
# outside winget (most of these were) are still removable.
$targets = @(
    # B-level
    "REDlauncher",
    "Epic Online Services",
    "Microsoft .NET Core SDK 3.1.426",
    "Paradox Launcher v2",
    "Apifox",
    "Unity Hub",
    "Xmind",
    "作家助手",
    # C-level
    "Docker Desktop",
    "飞书",
    "Windows Software Development Kit - Windows 10.0.22621.5040",
    "Windows Software Development Kit - Windows 10.0.26100.4654"
)

Write-Host "=== Before: $(Show-Free) ===" -ForegroundColor Cyan
Write-Host ""

$keys = @(
    'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*'
)

foreach ($name in $targets) {
    Write-Host "--- $name ---" -ForegroundColor Yellow
    $hits = Get-ItemProperty $keys -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -eq $name -or $_.DisplayName -like "*$name*" }
    if (-not $hits) {
        Write-Host "  not found in registry, skipping"
        continue
    }
    foreach ($hit in $hits) {
        $cmd = $hit.QuietUninstallString
        if (-not $cmd) { $cmd = $hit.UninstallString }
        if (-not $cmd) {
            Write-Host "  no UninstallString for '$($hit.DisplayName)', skipping"
            continue
        }
        Write-Host "  uninstalling: $($hit.DisplayName)  ($([int]($hit.EstimatedSize/1024)) MB reported)"

        if ($cmd -match '^\s*"([^"]+)"\s*(.*)$') {
            $exe = $matches[1]; $args = $matches[2]
        } elseif ($cmd -match '^(MsiExec\.exe.*)$' -or $cmd -match '^msiexec.*') {
            # Append /quiet /norestart
            $cmd2 = ($cmd -replace '/I\{', '/X{') + " /quiet /norestart"
            Write-Host "    cmd: $cmd2"
            cmd /c $cmd2 2>&1 | Out-Host
            continue
        } else {
            $exe = ($cmd -split ' ', 2)[0]; $args = if ($cmd.Length -gt $exe.Length) { $cmd.Substring($exe.Length+1) } else { '' }
        }

        # Add silent flags for common installers (best-effort).
        $silent = @{
            'innosetup' = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART'
            'nsis'      = '/S'
            'wix'       = '/quiet /norestart'
        }
        if ($args -notmatch '/quiet|/silent|/S\b|/VERYSILENT') {
            # Heuristic: if exe is unins000.exe (InnoSetup), add /VERYSILENT
            if ($exe -match 'unins\d+\.exe') { $args += ' /VERYSILENT /SUPPRESSMSGBOXES /NORESTART' }
            elseif ($exe -match 'msiexec') { $args += ' /quiet /norestart' }
        }

        Write-Host "    exe: $exe"
        Write-Host "    args: $args"
        Start-Process -FilePath $exe -ArgumentList $args -Wait -ErrorAction SilentlyContinue
    }
    Write-Host "  -> $(Show-Free)"
    Write-Host ""
}

Write-Host "=== Done ===" -ForegroundColor Cyan
Show-Free
