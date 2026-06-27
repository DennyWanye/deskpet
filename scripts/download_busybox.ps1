# P5-S2 (2026-05-10): fetch busybox-w32 (Ron Yorston's Win32 port).
#
# Run BEFORE building the MSI (the PyInstaller spec datas reference the
# downloaded file). One-time cost — the binary is committed to the repo
# so end users / CI don't need to download anything.
#
# Source: https://frippery.org/busybox/
#   Author: Ron Yorston (rys@gnu.org)
#   License: GPLv2
#
# Why busybox-w32 specifically:
#   * Single 700KB exe with sh + 200+ unix applets (ls, grep, sed,
#     awk, find, cat, head, tail, curl, ping, etc.)
#   * Zero install — drop-in shell replacement for cmd
#   * Run as: `busybox.exe sh -c "<command>"`
#   * Far smaller than bundling Git for Windows (~70 MB) but covers
#     ~95% of LLM-generated commands
#
# Usage:
#   pwsh -File scripts/download_busybox.ps1
#
# Idempotent: skips download if a valid file already exists at target.

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$targetDir = Join-Path $repoRoot 'resources/busybox-w32'
$targetFile = Join-Path $targetDir 'busybox.exe'
$readmeFile = Join-Path $targetDir 'README.md'

# 64-bit busybox is the right default for any machine made in the last
# decade. The 32-bit variant exists for ancient hardware; we don't care.
$url = 'https://frippery.org/files/busybox/busybox64.exe'

# Expected size lower bound — busybox-w32 is ~700KB. If the download
# came back tiny, something went wrong (404 page, captive portal, etc.).
$minBytes = 200 * 1024  # 200 KB minimum

if (Test-Path $targetFile) {
    $size = (Get-Item $targetFile).Length
    if ($size -ge $minBytes) {
        Write-Host "busybox.exe already present ($size bytes); skipping download."
        exit 0
    } else {
        Write-Host "busybox.exe exists but is suspiciously small ($size bytes); re-downloading."
    }
}

if (-not (Test-Path $targetDir)) {
    New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
}

Write-Host "Downloading busybox-w32 from $url ..."
try {
    Invoke-WebRequest -Uri $url -OutFile $targetFile -UseBasicParsing
} catch {
    Write-Error "Failed to download busybox: $_"
    exit 1
}

$size = (Get-Item $targetFile).Length
if ($size -lt $minBytes) {
    Remove-Item $targetFile
    Write-Error "Download completed but file is too small ($size bytes < $minBytes minimum). Aborted."
    exit 1
}

# Drop a README next to the binary recording provenance + license so
# anyone browsing the repo immediately sees where this came from.
@"
# busybox-w32

Single-file Win32 build of GNU busybox — Ron Yorston's port.

## Source
- Upstream:  https://frippery.org/busybox/
- Author:    Ron Yorston (rys@gnu.org)
- Variant:   busybox64.exe (64-bit)
- Downloaded: $(Get-Date -Format 'yyyy-MM-dd')

## License
GPLv2. The binary is redistributable when accompanied by a copy of the
license. Source code is available at the upstream link.

## Why we ship it
DeskPet's run_shell tool prefers Git Bash → bundled busybox → PowerShell
→ cmd. End users without Git installed still get a competent unix-like
shell so LLM-generated `ls / grep / sed / awk / find / cat | grep`
commands Just Work without code changes.

## How to refresh
Run `pwsh -File scripts/download_busybox.ps1` to re-fetch the latest
release.
"@ | Out-File -FilePath $readmeFile -Encoding utf8

Write-Host "Downloaded busybox-w32 to $targetFile ($size bytes)."
Write-Host "Provenance written to $readmeFile."
