---
name: verify-deskpet
description: SOP for verifying real DeskPet GUI artifacts via screenshot, real-coordinate click, and log assertion (windows-mcp E2E discipline)
triggers: [verify deskpet, 真机校验, 手测, manual test, windows-mcp, 真 e2e, real e2e, 验证桌宠]
user-invocable: false
requires_script: false
---

# verify-deskpet — 真机产物校验 SOP（给开发/测试 agent 用）

> ⚠️ 这是**工程 skill**，不是给终端用户的功能。用于把「改了代码」升级成
> 「真 E2E 证据」。不要在普通用户对话里 /invoke。

## 硬约束（HARD — 不可妥协）

用户要的不是 PASS 数量，是**真 E2E 证据**。绕过得来的 PASS 是负价值。

### 不算证据（全部禁止当 UI 测试证据）

- `ws://127.0.0.1:8100/*` WebSocket 直注后端
- `pytest` / `vitest` / `cargo` / `last_mile_smoke.py`
- `import` backend 查 registry / loader / keychain 文件存在
- `cmdkey /list` / boot log grep

以上全是**协议层 / 脚本 / 间接证据**，不替代真模拟点击。工具报错也**不许**
fallback 到这些，必须找 workaround 克服。

## 每个 testcase 的真测闭环（5 步，缺一不可）

1. **Snapshot / Screenshot** 当前界面（截图存盘
   `plans/manual-results-<date>/screenshots/`）。
2. **动作前 declare**：`坐标=(x,y) | 动作=click/type/drag | 期望=...`。
3. **真坐标点击 / 真输入**（不是调内部函数、不是脚本回放）。
4. **再 Screenshot** 验证 UI 变化。
5. **grep 日志判定**：抓 tauri dev 的重定向 log（backend structlog 全走 stderr →
   `Stdio::inherit()` → 落进 tauri dev log），确认真实出站行为，不是「函数返回值」。

## WebView2 真输入圣杯（SendInput）

WebView2 里 type 工具常打不进字符。用 Win32 `SendInput` 兜底：聚焦目标输入框
（先 Click）后用底层 SendInput 注入按键 / 用剪贴板 Ctrl+V。

## 中文 IME workaround

STA Runspace + `Clipboard.SetText("中文")` + Ctrl+V；焦点不在目标窗口先 Click
输入框聚焦再粘贴；用 backend log 确认消息真收到。

## 失败处理纪律

- 失败 **retry ≥3 次不同 workaround** 才能标「环境受限」。
- 跳过任何 case 必须**显式声明 + 具体理由 + 等用户确认**，不许静默跳过。

## 短路径偏置警觉 🚨

看到「我直接 WebSocket 验证 / pytest 覆盖了 / import 查一下 / 工具报错所以跳过 UI」
这类念头时**立刻停下，回到真模拟路径**。

## 例：验证「生成 PPT」产物

1. 启动桌宠（见 `run-deskpet` skill）+ 登录。
2. Screenshot 主界面 → declare 坐标 → Click 输入框 → SendInput/粘贴
   "帮我生成一份关于 X 的 PPT"。
3. Screenshot 等 ArtifactCard 出现。
4. grep tauri dev log 确认 `ppt_create` 工具真被调 + .pptx 真落盘。
5. Click ArtifactCard 打开 → Screenshot 确认真渲染。
