# Move the pet window to a known free area, then screenshot it.
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class M {
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr hAfter, int x, int y, int cx, int cy, uint f);
    [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr h, int x, int y, int w, int h2, bool repaint);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
    public static IntPtr HWND_TOPMOST = new IntPtr(-1);
    public const uint SWP_NOSIZE = 0x0001;
    public const uint SWP_NOZORDER = 0x0004;
    public const uint SWP_SHOWWINDOW = 0x0040;
}
'@
Add-Type -AssemblyName System.Windows.Forms,System.Drawing

$hwnd = [IntPtr]38210094

# Move pet to (100, 100) on primary monitor — clear of Claude Code's typical foreground
[M]::MoveWindow($hwnd, 100, 100, 375, 609, $true) | Out-Null
Start-Sleep -Milliseconds 800

# Verify new position
$r = New-Object M+RECT
[M]::GetWindowRect($hwnd, [ref]$r) | Out-Null
"new rect: ($($r.L), $($r.T)) - ($($r.R), $($r.B))"

# Screenshot exactly the new pet location
$b = New-Object System.Drawing.Bitmap(380, 615)
$g = [System.Drawing.Graphics]::FromImage($b)
$g.CopyFromScreen($r.L, $r.T, 0, 0, $b.Size)
$b.Save('G:/projects/deskpet/evidence/ui-test-07-pet-moved.png')
$g.Dispose(); $b.Dispose()
"saved snap of pet at ($($r.L), $($r.T))"
