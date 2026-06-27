# CDP E2E 升级 — Playwright 经 WebView2 DevTools 驱动

_2026-04-14 — 用 DOM 选择器替换像素定位_

## 旧方案的问题

`scripts/e2e/drive_full_ui.py` 用**像素颜色**定位控件，原因是 pyautogui 必须应对：
- Tauri WebView2 的 DPR=1.5（CSS 像素 ≠ 物理像素）
- 窗口位置变动（绝对坐标随之失效）
- 录音状态下 mic 按钮的 pulse 动画
- GBK 终端把中文状态输出都搞成乱码

结果：脚本脆弱、慢、每加一个控件就要手工调偏移，还读不到助手回复的文字。

## 新方案

在 debug 编译下开启 WebView2 的 **DevTools Protocol** 监听端口，用 Playwright 走 CDP 操作。选择器全是真实 DOM 的 `data-testid` —— 零像素计算、CSS 像素精度、与 DPR 无关。

### 改动文件

| 路径 | 变更 |
|---|---|
| `tauri-app/src-tauri/src/main.rs` | 仅 debug 构建设置 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222 --remote-allow-origins=*` |
| `tauri-app/src/App.tsx` | 给 mic / interrupt / chat-input / send / memory-toggle / chat bubbles 加 `data-testid` |
| `tauri-app/src/components/MemoryPanel.tsx` | 给 close / refresh / export / clear 变体 / 每行 / 每个删除按钮加 `data-testid` |
| `scripts/e2e/drive_via_cdp.py` | 新增 — Playwright CDP driver，替代 `drive_full_ui.py` |

### 新 driver 能做的事

```python
page.locator('[data-testid="chat-input"]').fill("你好！")
page.locator('[data-testid="send-button"]').click()
page.locator('[data-testid^="memory-turn-"]').count()       # 行数
page.locator('[data-testid="memory-delete-5"]').click()     # 删除特定 turn
```

- 用 `inner_text()` **直接读助手回复文字** —— 不再只靠人眼看截图
- 删除前后 **行数比对** → 真正的断言
- 读 mic 按钮的 `title` 属性 → "Start recording" vs "Stop recording"
- `force=True` + JS `el.click()` 绕过 Playwright 对 pulse 动画的稳定性检查

### 新测试输出

```
=== STEP: TEXT CHAT ===
[chat] sent; waiting for assistant bubble (had 0)...
[chat] PASS assistant reply: 我是一个大型语言模型，致力于成为您的智能伙伴...

=== STEP: MEMORY PANEL ===
[mem] turn rows rendered: 59
[mem] deleting turn id=6
[mem] PASS delete worked (59 -> 58)
[mem] PASS panel closed

=== STEP: MIC TOGGLE ===
[mic] initial title='Start recording'
[mic] after-click-1 title='Stop recording'
[mic] after-click-2 title='Start recording'
[mic] PASS flip1=True flip2=True

=== STEP: ESC INTERRUPT ===
[esc] reply len after-esc=62 after-3s=62 growth=0
[esc] PASS stream halted by Esc

=== SUMMARY ===
  chat      PASS
  memory    PASS
  mic       PASS
  esc       PASS
```

## 速度对比

| 动作 | `drive_full_ui.py`（像素法） | `drive_via_cdp.py`（CDP） |
|---|---|---|
| 定位按钮 | 1–3s 全屏扫描 | 1–5ms 选择器 |
| 点击 + 验证 | 截图 + 肉眼 diff | 读 DOM 属性 |
| 助手回复文字 | 拿不到 | 直接 `inner_text()` |
| 记忆行数 | 无法做到 | `locator.count()` |

## 踩过的坑

- **Cargo 不重新嵌入资源**：Tauri 在编译时把 `frontendDist` 的内容烧进二进制。刷新 `dist/` 之后必须 `touch build.rs` + `cargo build` 才能重新嵌入，否则 WebView2 加载的是旧 bundle 名。
- **Pulse 动画挡 Playwright 点击**：录音状态下 mic 按钮带 `animation: pulse 1.5s infinite`。Playwright 的 stability 检查把动画元素判为"不稳定"，连 `force=True` 都通不过。解决：`mic.evaluate("el => el.click()")` 直接派发 DOM 事件。
- **会话状态会跨测试残留**：记忆上下文一大（50+ turns）Ollama 就很慢，首 token 可能超 30s。驱动脚本在 chat 步骤前 `page.reload()` 起干净会话即可。
- **TypeScript strict-null 卡 build**：`npm run build` 里先跑 `tsc -b`，会因 Live2DCanvas.tsx 的遗留 null 报错。E2E 用途下直接 `npx vite build` 跳过类型检查。

## 运行方式

```bash
# 一次性装依赖
backend/.venv/Scripts/pip.exe install playwright

# 启动后端（一个终端）
backend/.venv/Scripts/python.exe -m uvicorn backend.main:app --port 8100

# 启动 Vite（另一个终端，仅 HMR 开发要）
cd tauri-app && npm run dev

# 编译 + 启动 debug 版（带 CDP 端口）
cd tauri-app && npx vite build && touch src-tauri/build.rs && \
  cargo -C src-tauri build && \
  DESKPET_DEV_MODE=1 src-tauri/target/debug/deskpet.exe &

# 确认 CDP 已起
curl http://localhost:9222/json/version

# 跑全套 E2E
backend/.venv/Scripts/python.exe scripts/e2e/drive_via_cdp.py

# 只跑某一项
backend/.venv/Scripts/python.exe scripts/e2e/drive_via_cdp.py --only memory
```

## 发布版的安全性

`main.rs` 里 CDP 端口开关用 `#[cfg(debug_assertions)]` 门控，**release 构建不会暴露 9222**。线上版不受影响。

## 产物

- `scripts/e2e/drive_via_cdp.py` — 250 行，4 个步骤，幂等
- `scripts/e2e/shots_cdp/*.png` — 每步前后截图
