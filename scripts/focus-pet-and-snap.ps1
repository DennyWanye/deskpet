# Force the pet window to top of Z-order and screenshot it.
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class Z {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr hAfter, int x, int y, int cx, int cy, uint f);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    public static IntPtr HWND_TOPMOST = new IntPtr(-1);
    public const uint SWP_NOMOVE = 0x0002;
    public const uint SWP_NOSIZE = 0x0001;
    public const uint SWP_SHOWWINDOW = 0x0040;
}
'@
Add-Type -AssemblyName System.Windows.Forms,System.Drawing

$hwnd = [IntPtr]38210094
[Z]::ShowWindow($hwnd, 5) | Out-Null               # SW_SHOW
[Z]::SetWindowPos($hwnd, [Z]::HWND_TOPMOST, 0, 0, 0, 0, ([Z]::SWP_NOMOVE -bor [Z]::SWP_NOSIZE -bor [Z]::SWP_SHOWWINDOW)) | Out-Null
[Z]::BringWindowToTop($hwnd) | Out-Null
[Z]::SetForegroundWindow($hwnd) | Out-Null
Start-Sleep -Milliseconds 500

# Screenshot the exact rect
$b = New-Object System.Drawing.Bitmap(375, 609)
$g = [System.Drawing.Graphics]::FromImage($b)
$g.CopyFromScreen(2140, 640, 0, 0, $b.Size)
$b.Save('/path/to/deskpet/evidence/ui-test-06-pet-foregrounded.png')
$g.Dispose(); $b.Dispose()
"focused + saved"
