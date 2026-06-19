# S1 manual-test launcher: starts the worktree's vite dev server with a
# specific VITE_PET_ENGINE backend. Used by .claude/launch.json so
# preview MCP can boot the renderer in isolation from Tauri/backend
# (which the S1 changes don't touch).
#
# Usage:
#   powershell -File scripts/preview-vite.ps1 -Backend live2d
#   powershell -File scripts/preview-vite.ps1 -Backend null
param(
    [ValidateSet('sprite')]
    [string]$Backend = 'sprite',
    [int]$Port = 5473
)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$env:VITE_PET_ENGINE = $Backend
$env:DESKPET_VITE_PORT = "$Port"

Write-Host "=== DeskPet S1 preview vite ===" -ForegroundColor Cyan
Write-Host "  VITE_PET_ENGINE = $Backend"
Write-Host "  port            = $Port"
Write-Host "  cwd             = $root\tauri-app"
Write-Host ""

Set-Location (Join-Path $root "tauri-app")
& npx vite --port $Port --strictPort --host 127.0.0.1
