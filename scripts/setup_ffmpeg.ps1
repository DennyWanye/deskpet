# scripts/setup_ffmpeg.ps1
#
# 一次性下载 portable ffmpeg.exe 到 backend/bin/ 供 voice pipeline
# (Task 9: MP3 -> PCM s16le 24kHz pipe) 调用。仓内不入 git，靠这个脚本
# 在新机器 / CI 上自动准备。
#
# 用法：
#   pwsh scripts/setup_ffmpeg.ps1            # 没装才装
#   pwsh scripts/setup_ffmpeg.ps1 -Force     # 强制重装
#
# 来源：BtbN/FFmpeg-Builds (GitHub release, GPL win64 build, 无外部 DLL
# 依赖 —— 静态链接，单 .exe 即可工作)。

[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$RepoRoot    = Split-Path -Parent $PSScriptRoot
$BinDir      = Join-Path $RepoRoot "backend\bin"
$FfmpegExe   = Join-Path $BinDir   "ffmpeg.exe"
$DownloadUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"

if ((Test-Path $FfmpegExe) -and (-not $Force)) {
    Write-Host "[setup_ffmpeg] already present: $FfmpegExe" -ForegroundColor Green
    & $FfmpegExe -version | Select-Object -First 1
    exit 0
}

New-Item -ItemType Directory -Path $BinDir -Force | Out-Null

$stamp  = [Guid]::NewGuid().ToString("N").Substring(0,8)
$tmpZip = Join-Path $env:TEMP "deskpet-ffmpeg-$stamp.zip"
$tmpDir = Join-Path $env:TEMP "deskpet-ffmpeg-$stamp"

try {
    Write-Host "[setup_ffmpeg] downloading $DownloadUrl" -ForegroundColor Cyan
    Write-Host "[setup_ffmpeg] (~200MB, 1-2 min on normal network)" -ForegroundColor DarkGray

    # Invoke-WebRequest 的进度条在大文件 + Git Bash 嵌套调用下会把 PS 卡
    # 到内存爆掉，所以关掉进度显示。ProgressPreference=SilentlyContinue
    # 能让 IWR 跑到十倍速。
    $prevProgress = $ProgressPreference
    $ProgressPreference = 'SilentlyContinue'
    try {
        Invoke-WebRequest -Uri $DownloadUrl -OutFile $tmpZip -UseBasicParsing
    } finally {
        $ProgressPreference = $prevProgress
    }

    if (-not (Test-Path $tmpZip)) {
        throw "download failed: file not created"
    }
    $sz = (Get-Item $tmpZip).Length
    if ($sz -lt 50MB) {
        throw "download too small: $sz bytes (expect ~200MB)"
    }
    Write-Host "[setup_ffmpeg] downloaded $([math]::Round($sz/1MB,1)) MB" -ForegroundColor Cyan

    Write-Host "[setup_ffmpeg] extracting ..." -ForegroundColor Cyan
    Expand-Archive -Path $tmpZip -DestinationPath $tmpDir -Force

    $extractedExe = Get-ChildItem -Path $tmpDir -Filter "ffmpeg.exe" -Recurse -File |
                    Select-Object -First 1
    if (-not $extractedExe) {
        throw "ffmpeg.exe not found inside archive — BtbN layout may have changed"
    }

    Copy-Item -Path $extractedExe.FullName -Destination $FfmpegExe -Force
    Write-Host "[setup_ffmpeg] installed -> $FfmpegExe" -ForegroundColor Green

    # Self-test
    $ver = & $FfmpegExe -version 2>&1 | Select-Object -First 1
    Write-Host "[setup_ffmpeg] $ver" -ForegroundColor Green
}
finally {
    if (Test-Path $tmpZip) { Remove-Item $tmpZip -Force -ErrorAction SilentlyContinue }
    if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue }
}
