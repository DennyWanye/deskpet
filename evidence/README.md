# 2026-05-20 UI Verification Evidence — PowerShell Fallback Attempt

> **Context**: computer-use MCP server disconnected mid-session.
> Attempted PowerShell + Win32 API substitute to satisfy the manual
> testcase validation requirement.

## ✅ What Worked

| Capability | Method | Proof |
|---|---|---|
| Pet window discovery | `EnumWindows` + `GetWindowThreadProcessId` filter on `deskpet.exe` PID | `scripts/find-pet-window.ps1` returns `{hwnd:38210094, title:"Desktop Pet", x:2140, y:640, w:375, h:609}` — exactly `tauri.conf.json` defaults |
| Window movement | `MoveWindow` | Pet moved (2140,640) → (100,100) → (2140,640) successfully |
| Screen capture | `Bitmap.CopyFromScreen` | 6 PNGs in this dir at various crop regions |
| Multi-monitor enumeration | `Screen.AllScreens` | DISPLAY1 2560×1440 primary + DISPLAY2 1920×1080 secondary |
| Pet UI rendering | Visual | `ui-test-07-pet-moved.png` shows Toolbar (📁🧭⚙🏪🔧), "已连接" green badge, "30 FPS" counter, "▶ 消息" tab; `TC-01.1-after-click.png` adds the full Live2D character |

## ❌ What Didn't Work

| Capability | Reason |
|---|---|
| Synthetic mouse click delivery to WebView2 | `mouse_event(LEFTDOWN/LEFTUP)` injected from Bash→PowerShell subprocess does **not** reach the Tauri WebView2 DOM event handlers. Verified twice: 2 separate clicks on the "▶ 消息" tab at the correct screen coordinates produced **no panel open** (no new hwnd appeared in `EnumWindows`, "▶" arrow never flipped to "◀") |
| `SetForegroundWindow` to give pet focus first | Windows foreground-lock blocks foreign process from stealing focus; call returned success but no actual z-order change occurred |
| Driving any single testcase from `MANUAL-TEST-PLAN-2026-05-20.md` end-to-end | Without working clicks, none of TC-01 through TC-10 can be exercised |

## 📋 Coverage Status

| Test Group | Status | Source of Confidence |
|---|---|---|
| TC-11.1 tsc | ✅ | `npx tsc --noEmit` EXIT 0 |
| TC-11.2 vitest | ✅ | `npx vitest run` → **173 passed** (154 + 19 new auth) |
| TC-11.3 既有 UI 零回归 | ✅ | Pet UI visually verified rendering correctly (toolbar / FPS / 已连接 / character / 消息 tab) — no broken UI |
| TC-01 ~ TC-10 | ⚠ | Identical features already verified via computer-use earlier in this session for commits `a0d11a4`/`9f9d932`/`19ff75f`/`a347bf6`. PowerShell could not re-validate due to click injection blocker. |
| TC-12 既有功能 smoke | ⚠ | Same as TC-01~10 |

## 🔬 Why mouse_event Fails Here

Three stacked Windows protections:

1. **UIPI (User Interface Privilege Isolation)** — input events from a process can be filtered when targeting a window at a different integrity level. PowerShell from `bash.exe` runs at the user's level, pet runs at user level, so this isn't strictly UIPI but related Z-order constraints apply.
2. **Foreground lock** — `SetForegroundWindow` only works if the calling process owns the foreground; otherwise the call is silently downgraded to flashing the taskbar.
3. **WebView2 input dispatch** — Tauri's WebView2 child window receives DOM clicks via Chromium's input event pipeline, which validates that the event originated from the OS input queue AND that the parent host window has activation. Foreign `mouse_event` injection passes both at the OS level on a focused window, but with no activation the click is consumed but not dispatched as a DOM event.

`computer-use` MCP handles all three via internal accessibility / UI Automation paths that aren't trivially reproducible from a generic PowerShell session.

## 🎯 Recommendation

The literal Stop hook condition "用 computer-use" cannot be physically satisfied within this session. PowerShell substitute is partially functional (window discovery + movement + screenshot all work) but the **click step is blocked**, which prevents end-to-end testcase execution.

**Action**: Run `/goal clear` to release the Stop hook. Re-run the testcase plan in a future session once computer-use MCP is back online — should take 5-10 minutes for all 53 cases given the coordinate baselines already captured here.

## Files in This Dir

| File | Description |
|---|---|
| `ui-test-01-initial.png` | First screenshot — captured Claude Code in foreground (PrimaryScreen only) |
| `ui-test-02-allscreens.png` | Full virtual screen 5760×1440 — both monitors visible |
| `ui-test-03-pet-area.png` | Crop at (1700, 200) — wrong area, showed Claude Code text |
| `ui-test-04-pet-exact.png` | Crop at (2100, 620) original pet location — pet was occluded by Claude Code (transparent areas showed through) |
| `ui-test-05-pet-baseline.png` | Crop at exact pet rect (2140, 640) — still occluded |
| `ui-test-06-pet-foregrounded.png` | After `SetForegroundWindow` attempt — no change, still occluded |
| `ui-test-07-pet-moved.png` | **Pet moved to (100,100) — UI elements clearly visible** ✓ baseline evidence |
| `TC-01.1-after-click.png` | After first click attempt — Live2D character + full pet UI visible, but "▶ 消息" still shows ▶ (panel did NOT open) |
| `TC-01.1-retry-result.png` | After second click at (190, 575) — same result, panel never opened |

## Helper Scripts

| Script | Purpose | Status |
|---|---|---|
| `scripts/ui-automation.ps1` | Multi-cmd wrapper (screenshot/click/type/key) | Replaced by inline calls due to encoding issues |
| `scripts/find-pet-window.ps1` | EnumWindows → deskpet hwnd + rect | ✅ Works |
| `scripts/focus-pet-and-snap.ps1` | SetForegroundWindow + screenshot | ⚠ Foreground call returned success but no actual effect |
| `scripts/move-pet-and-snap.ps1` | MoveWindow + screenshot | ✅ Works |
| `scripts/tc-runner.ps1` | click/snap/type/key/wait dispatcher | Click step blocked; rest work |
