# Find deskpet windows by enumerating top-level windows and matching process id.
# Avoids the $pid name collision (PowerShell built-in for the current process).
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public class WinFinder {
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc f, IntPtr p);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint procId);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    public delegate bool EnumProc(IntPtr h, IntPtr p);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
}
'@

$targetPids = (Get-Process deskpet -ErrorAction SilentlyContinue).Id
if (-not $targetPids) { Write-Error "deskpet.exe not running"; exit 1 }

$found = New-Object System.Collections.ArrayList
$cb = [WinFinder+EnumProc]{
    param($h, $p)
    if (-not [WinFinder]::IsWindowVisible($h)) { return $true }
    $procId = [uint32]0
    [WinFinder]::GetWindowThreadProcessId($h, [ref]$procId) | Out-Null
    if ($targetPids -notcontains $procId) { return $true }
    $sb = New-Object System.Text.StringBuilder 256
    [WinFinder]::GetWindowText($h, $sb, 256) | Out-Null
    $r = New-Object WinFinder+RECT
    [WinFinder]::GetWindowRect($h, [ref]$r) | Out-Null
    $w = $r.R - $r.L
    $hgt = $r.B - $r.T
    if ($w -lt 50 -or $hgt -lt 50) { return $true }   # skip tiny / off-screen
    [void]$found.Add([PSCustomObject]@{
        pid = $procId
        hwnd = $h
        title = $sb.ToString()
        x = $r.L
        y = $r.T
        w = $w
        h = $hgt
    })
    return $true
}
[WinFinder]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null

$found | ConvertTo-Json -Compress
