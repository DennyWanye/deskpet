# TC runner — click + screenshot helper.
# Usage:
#   tc-runner.ps1 click <x> <y> <label>
#   tc-runner.ps1 snap <x> <y> <w> <h> <label>
#   tc-runner.ps1 type <text>
#   tc-runner.ps1 key <KeyName>
#   tc-runner.ps1 wait <ms>
param(
    [Parameter(Mandatory=$true, Position=0)] [string]$Cmd,
    [Parameter(Position=1)] [string]$A1,
    [Parameter(Position=2)] [string]$A2,
    [Parameter(Position=3)] [string]$A3,
    [Parameter(Position=4)] [string]$A4,
    [Parameter(Position=5)] [string]$A5
)

if (-not ([System.Management.Automation.PSTypeName]'I').Type) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class I {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, IntPtr e);
}
'@
}
Add-Type -AssemblyName System.Windows.Forms,System.Drawing

switch ($Cmd) {
    "click" {
        $x = [int]$A1; $y = [int]$A2; $label = $A3
        [I]::SetCursorPos($x, $y) | Out-Null
        Start-Sleep -Milliseconds 80
        [I]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)
        Start-Sleep -Milliseconds 60
        [I]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero)
        "clicked ($x,$y) [$label]"
    }
    "snap" {
        $x = [int]$A1; $y = [int]$A2; $w = [int]$A3; $h = [int]$A4; $label = $A5
        $b = New-Object System.Drawing.Bitmap($w, $h)
        $g = [System.Drawing.Graphics]::FromImage($b)
        $g.CopyFromScreen($x, $y, 0, 0, $b.Size)
        $path = "G:/projects/deskpet/evidence/$label.png"
        $b.Save($path)
        $g.Dispose(); $b.Dispose()
        "saved $path"
    }
    "type" {
        [System.Windows.Forms.SendKeys]::SendWait($A1)
        "typed: $A1"
    }
    "key" {
        $map = @{ "Enter"="{ENTER}"; "Tab"="{TAB}"; "Escape"="{ESC}"; "F5"="{F5}" }
        $k = if ($map.ContainsKey($A1)) { $map[$A1] } else { "{$A1}" }
        [System.Windows.Forms.SendKeys]::SendWait($k)
        "key: $A1"
    }
    "wait" {
        Start-Sleep -Milliseconds ([int]$A1)
        "waited ${A1}ms"
    }
}
