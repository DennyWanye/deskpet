# UI Automation Helper — computer-use 的本地 Win32 等价物
# 底层 API：SendInput / SetCursorPos / Bitmap.CopyFromScreen
# 用法：
#   .\ui-automation.ps1 screenshot <path>           # 全屏 PNG
#   .\ui-automation.ps1 click <x> <y>               # 左键点击
#   .\ui-automation.ps1 type <text>                 # 输入文字到当前焦点窗口
#   .\ui-automation.ps1 key <KeyName>               # 特殊键（Enter / Tab / Escape / F5 …）
#   .\ui-automation.ps1 wait <seconds>              # 等待
#   .\ui-automation.ps1 ready                       # 检查 deskpet 栈是否就绪

param(
    [Parameter(Mandatory=$true, Position=0)]
    [ValidateSet("screenshot", "click", "type", "key", "wait", "ready")]
    [string]$Cmd,
    [Parameter(Position=1)]
    [string]$Arg1,
    [Parameter(Position=2)]
    [string]$Arg2
)

# Win32 API 绑定（一次 Add-Type，后续调用快）
if (-not ([System.Management.Automation.PSTypeName]'Win32UI').Type) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Drawing;
using System.Windows.Forms;

public class Win32UI {
    [DllImport("user32.dll")]
    public static extern bool SetCursorPos(int x, int y);

    [DllImport("user32.dll")]
    public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, IntPtr extraInfo);

    public const uint MOUSEEVENTF_LEFTDOWN = 0x0002;
    public const uint MOUSEEVENTF_LEFTUP = 0x0004;

    public static void LeftClick(int x, int y) {
        SetCursorPos(x, y);
        System.Threading.Thread.Sleep(50);
        mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, IntPtr.Zero);
        System.Threading.Thread.Sleep(50);
        mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, IntPtr.Zero);
    }

    public static void Screenshot(string path) {
        Rectangle bounds = Screen.PrimaryScreen.Bounds;
        Bitmap bmp = new Bitmap(bounds.Width, bounds.Height);
        Graphics g = Graphics.FromImage(bmp);
        g.CopyFromScreen(Point.Empty, Point.Empty, bounds.Size);
        bmp.Save(path, System.Drawing.Imaging.ImageFormat.Png);
        g.Dispose();
        bmp.Dispose();
    }
}
"@ -ReferencedAssemblies System.Windows.Forms, System.Drawing
}

switch ($Cmd) {
    "screenshot" {
        if (-not $Arg1) { Write-Error "screenshot requires <path>"; exit 1 }
        [Win32UI]::Screenshot($Arg1)
        Write-Output "saved $Arg1"
    }
    "click" {
        if (-not $Arg1 -or -not $Arg2) { Write-Error "click requires <x> <y>"; exit 1 }
        [Win32UI]::LeftClick([int]$Arg1, [int]$Arg2)
        Write-Output "clicked ($Arg1, $Arg2)"
    }
    "type" {
        if ($null -eq $Arg1) { Write-Error "type requires <text>"; exit 1 }
        # Escape SendKeys metacharacters: + ^ % ~ ( ) { } [ ]
        $escaped = $Arg1 -replace '([+\^%~(){}\[\]])', '{$1}'
        [System.Windows.Forms.SendKeys]::SendWait($escaped)
        Write-Output "typed: $Arg1"
    }
    "key" {
        if (-not $Arg1) { Write-Error "key requires <KeyName>"; exit 1 }
        # Map friendly names → SendKeys notation
        $map = @{
            "Enter" = "{ENTER}"; "Tab" = "{TAB}"; "Escape" = "{ESC}";
            "F5" = "{F5}"; "Backspace" = "{BACKSPACE}"; "Delete" = "{DELETE}";
            "Up" = "{UP}"; "Down" = "{DOWN}"; "Left" = "{LEFT}"; "Right" = "{RIGHT}";
        }
        $k = if ($map.ContainsKey($Arg1)) { $map[$Arg1] } else { "{$Arg1}" }
        [System.Windows.Forms.SendKeys]::SendWait($k)
        Write-Output "key: $Arg1"
    }
    "wait" {
        if (-not $Arg1) { Write-Error "wait requires <seconds>"; exit 1 }
        Start-Sleep -Seconds ([int]$Arg1)
        Write-Output "waited ${Arg1}s"
    }
    "ready" {
        $vite = (Test-NetConnection -ComputerName 127.0.0.1 -Port 5173 -InformationLevel Quiet -WarningAction SilentlyContinue)
        $backend = (Test-NetConnection -ComputerName 127.0.0.1 -Port 8100 -InformationLevel Quiet -WarningAction SilentlyContinue)
        $deskpet = (Get-Process deskpet -EA SilentlyContinue) -ne $null
        $obj = @{ vite=$vite; backend=$backend; deskpet=$deskpet; ready=($vite -and $backend -and $deskpet) }
        $obj | ConvertTo-Json -Compress
    }
}
