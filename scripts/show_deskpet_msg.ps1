# Bring the real "DeskPet · 消息" chat panel window into view + foreground, so
# we can really click + type into it. Also nudge the off-screen pet window back
# on-screen as a fallback. Pass the message-panel HWND as arg.
param([int]$Hwnd = 1835050)
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class U {
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint f);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  public struct RECT { public int L, T, R, B; }
}
"@
$h = [IntPtr]$Hwnd
$SW_RESTORE = 9; $SW_SHOW = 5
$HWND_TOP = [IntPtr]::Zero
$SWP_SHOWWINDOW = 0x40
[void][U]::ShowWindow($h, $SW_RESTORE)
[void][U]::ShowWindow($h, $SW_SHOW)
# move to a clearly visible spot on the primary monitor, size 480x640
[void][U]::SetWindowPos($h, $HWND_TOP, 300, 150, 480, 640, $SWP_SHOWWINDOW)
[void][U]::SetForegroundWindow($h)
$r = New-Object 'U+RECT'; [void][U]::GetWindowRect($h, [ref]$r)
"msg-panel now at L=$($r.L) T=$($r.T) R=$($r.R) B=$($r.B)"
