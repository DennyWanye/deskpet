# Enumerate top-level windows owned by the dev deskpet.exe (g:\projects path),
# print handle + rect + visibility + title, so we can relocate the pet into view.
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr l);
  public delegate bool EnumWindowsProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr h);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  public struct RECT { public int L, T, R, B; }
}
"@
$targets = Get-Process -Name deskpet -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '/path/to/deskpet*' } | Select-Object -ExpandProperty Id
"dev deskpet pids: $($targets -join ', ')"
$script:rows = @()
$cb = [W+EnumWindowsProc]{
  param($h, $l)
  $pid2 = 0; [void][W]::GetWindowThreadProcessId($h, [ref]$pid2)
  if ($targets -contains $pid2) {
    $r = New-Object 'W+RECT'; [void][W]::GetWindowRect($h, [ref]$r)
    $len = [W]::GetWindowTextLength($h); $sb = New-Object System.Text.StringBuilder ($len+1)
    [void][W]::GetWindowText($h, $sb, $sb.Capacity)
    $vis = [W]::IsWindowVisible($h)
    $script:rows += [pscustomobject]@{ H=$h; PID=$pid2; Vis=$vis; L=$r.L; T=$r.T; R=$r.R; B=$r.B; W=($r.R-$r.L); Ht=($r.B-$r.T); Title=$sb.ToString() }
  }
  return $true
}
[void][W]::EnumWindows($cb, [IntPtr]::Zero)
$script:rows | Sort-Object Vis -Descending | Format-Table -AutoSize | Out-String -Width 200
