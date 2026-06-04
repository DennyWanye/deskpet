# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Hybrid 真测 — CDP 侦察桌宠 webview 精确坐标 + windows-mcp SendInput 真点.

复用 plans/manual-results-2026-05-26-master/hybrid_winmcp_test.py 验证过的方法:
  - CDP (9222) 拿元素 getBoundingClientRect 中心 + window.screenX/Y → logical 坐标
  - PowerShell SetCursorPos + SendInput 真点 (DPI-unaware 用 logical)

目标: 找登录窗关闭按钮 → 拿精确坐标 → SendInput 点 → 验证关闭.
"""
import asyncio
import json
import subprocess
import sys
import time
import urllib.request

import websockets


def find_pet_page():
    d = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
    pages = [t for t in d if t.get("type") == "page"]
    print(f"[CDP] {len(pages)} pages:")
    for p in pages:
        print(f"  - {p.get('url','')[:80]}")
    # 桌宠主窗口 (tauri.localhost 或 localhost:5573)
    for p in pages:
        u = p.get("url", "")
        if "tauri.localhost" in u or "5573" in u or "localhost" in u:
            return p
    return pages[0] if pages else None


async def evaluate(ws, expr, mid):
    await ws.send(json.dumps({
        "id": mid, "method": "Runtime.evaluate",
        "params": {"expression": expr, "returnByValue": True, "awaitPromise": True},
    }))
    while True:
        m = json.loads(await ws.recv())
        if m.get("id") == mid:
            return m.get("result", {}).get("result", {}).get("value")


def sendinput_click(lx, ly):
    """PowerShell SetCursorPos(logical) + SendInput — DPI-unaware 用 logical 坐标."""
    script = f"""
Add-Type -TypeDefinition @'
using System; using System.Runtime.InteropServices;
public class W {{
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern bool GetCursorPos(out P p);
  [StructLayout(LayoutKind.Sequential)] public struct P {{ public int X,Y; }}
  [StructLayout(LayoutKind.Sequential)] public struct MI {{ public int dx,dy; public uint mouseData,dwFlags,time; public IntPtr dwExtraInfo; }}
  [StructLayout(LayoutKind.Explicit, Size=40)] public struct IN_ {{ [FieldOffset(0)] public uint type; [FieldOffset(8)] public MI mi; }}
  [DllImport("user32.dll")] public static extern uint SendInput(uint n, IN_[] i, int sz);
}}
'@ -ErrorAction SilentlyContinue
[W]::SetCursorPos({lx}, {ly}) | Out-Null
Start-Sleep -Milliseconds 150
$p = New-Object W+P; [W]::GetCursorPos([ref]$p) | Out-Null
$d = New-Object W+IN_; $d.type=0; $d.mi=New-Object W+MI; $d.mi.dwFlags=0x0002
$u = New-Object W+IN_; $u.type=0; $u.mi=New-Object W+MI; $u.mi.dwFlags=0x0004
$sz = [Runtime.InteropServices.Marshal]::SizeOf([type][W+IN_])
$r1 = [W]::SendInput(1, @($d), $sz)
Start-Sleep -Milliseconds 60
$r2 = [W]::SendInput(1, @($u), $sz)
Write-Output "cursor_landed=($($p.X),$($p.Y)) DOWN=$r1 UP=$r2"
"""
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    return (r.stdout + r.stderr).strip()


async def main():
    target = find_pet_page()
    if not target:
        print("[FAIL] 没找到桌宠 page")
        return 1
    print(f"\n[CDP] 连 {target['url'][:60]}")

    async with websockets.connect(target["webSocketDebuggerUrl"], max_size=None) as ws:
        # 1. 侦察登录窗 — 列出所有 button + 它们的精确坐标
        probe = await evaluate(ws, r"""
(() => {
  const btns = Array.from(document.querySelectorAll('button'));
  const out = btns.map(b => {
    const r = b.getBoundingClientRect();
    return {
      text: (b.textContent || b.getAttribute('aria-label') || '').trim().slice(0, 20),
      cssX: Math.round(r.left + r.width/2),
      cssY: Math.round(r.top + r.height/2),
      w: Math.round(r.width), h: Math.round(r.height),
      visible: r.width > 0 && r.height > 0,
    };
  }).filter(b => b.visible);
  return {
    screenX: window.screenX, screenY: window.screenY,
    dpr: window.devicePixelRatio,
    innerW: window.innerWidth, innerH: window.innerHeight,
    buttons: out,
  };
})()
""", 1)
        if not probe:
            print("[FAIL] CDP probe 返空")
            return 1

        print(f"\n[CDP 侦察] window.screenX/Y=({probe['screenX']},{probe['screenY']}) dpr={probe['dpr']} inner={probe['innerW']}x{probe['innerH']}")
        print(f"[CDP 侦察] 可见按钮 {len(probe['buttons'])} 个:")
        for b in probe["buttons"]:
            print(f"   '{b['text']}' css-center=({b['cssX']},{b['cssY']}) {b['w']}x{b['h']}")

        # 2. 找登录窗关闭按钮（× / 关闭 / close）或登录按钮
        close_btn = None
        for b in probe["buttons"]:
            t = b["text"].lower()
            if "×" in b["text"] or "✕" in b["text"] or "close" in t or "关闭" in b["text"]:
                close_btn = b
                break
        # fallback: 找"登录"按钮（大目标，容错高）
        login_btn = next((b for b in probe["buttons"] if "登录" in b["text"] or "login" in b["text"].lower()), None)

        test_btn = close_btn or login_btn
        if not test_btn:
            print("\n[INFO] 没找到登录窗按钮 — 可能已登录或无 modal")
            print("[INFO] 改测桌宠任意可见按钮验证 SendInput 能点 WebView2")
            test_btn = probe["buttons"][0] if probe["buttons"] else None
        if not test_btn:
            print("[FAIL] 无可点按钮")
            return 1

        # 3. CSS 坐标 → 屏幕 logical 坐标（hybrid 验证过的公式）
        logical_x = probe["screenX"] + test_btn["cssX"]
        logical_y = probe["screenY"] + test_btn["cssY"]
        print(f"\n[目标] 按钮 '{test_btn['text']}' css-center=({test_btn['cssX']},{test_btn['cssY']})")
        print(f"[目标] 屏幕 logical = screenX({probe['screenX']}) + cssX({test_btn['cssX']}) = ({logical_x},{logical_y})")

        # 4. SendInput 真点
        print(f"\n[windows-mcp] SetCursorPos+SendInput @ logical({logical_x},{logical_y})")
        click_result = sendinput_click(logical_x, logical_y)
        print(f"[windows-mcp] {click_result}")

        await asyncio.sleep(0.6)

        # 5. CDP 验证点击效果 — 登录窗还在吗 + activeElement
        after = await evaluate(ws, r"""
(() => {
  const modal = document.querySelector('[class*="modal"], [class*="login"], [class*="overlay"]');
  const btnCount = document.querySelectorAll('button').length;
  const ae = document.activeElement;
  return {
    btnCount,
    activeTag: ae ? ae.tagName : null,
    activeTestId: ae ? ae.getAttribute('data-testid') : null,
    bodyTextSample: document.body.innerText.slice(0, 80),
  };
})()
""", 2)
        print(f"\n[CDP 验证] 点击后: 按钮数={after['btnCount']} activeElement={after['activeTag']}/{after.get('activeTestId')}")
        print(f"[CDP 验证] body: {after['bodyTextSample'][:60]}")

        # 判定
        before_count = len(probe["buttons"])
        after_count = after["btnCount"]
        if close_btn and after_count < before_count:
            print(f"\n[PASS] ✓ 登录窗关闭了 (按钮 {before_count}→{after_count}) — SendInput 真点 WebView2 成功!")
            return 0
        elif after.get("activeTestId") or after["activeTag"] in ("INPUT", "TEXTAREA", "BUTTON"):
            print(f"\n[PASS] ✓ SendInput 真改变了 WebView2 状态 (activeElement={after['activeTag']})")
            return 0
        else:
            print(f"\n[INCONCLUSIVE] 点击后状态变化不明显，需截图人工确认")
            return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
