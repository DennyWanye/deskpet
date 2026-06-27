# VN 风格对话栏 实施报告
_2026-04-14 — 桌宠 UI 遮挡修复_

## 问题

旧版 `App.tsx` 使用 `messages.slice(-5).map(...)` 在屏幕底部堆叠最多 5 条聊天气泡，容器 `maxHeight: 200px` + `bottom: 55px` 共计吃掉屏幕下半 ~22% 的可视面积，遮住了 Live2D 人物的下半身。多条气泡还会随每一轮新消息不停重排，让 TTS 同步时的视觉跳动很晕。

## 方案

Galgame / VN（Visual Novel）风格底栏：

- **底栏**（`DialogBar.tsx`）：屏幕底部固定 60px 对话框，任何时刻只渲染最新一条助手回复；新消息直接替换旧消息，无动画过渡；右上角挂 💬 按钮进入完整历史。
- **用户小气泡**（`UserBubble.tsx`）：发送后在输入框上方弹出一个 2s 自动淡出的小气泡，给"已发出"的心理确认；不进对话历史。
- **会话历史面板**（`ChatHistoryPanel.tsx`）：点 💬 展开本会话全部消息；复用 `MemoryPanel` 的遮罩样式保持视觉一致；只读、不跨会话（跨会话历史仍由 S14 的 `MemoryPanel` 覆盖）。

## 改动清单

| 路径 | 变更 | 说明 |
|---|---|---|
| `tauri-app/src/components/DialogBar.tsx` | 新增 | 60px 底栏 + 💬 切换按钮；`aria-label` 覆盖 |
| `tauri-app/src/components/UserBubble.tsx` | 新增 | 2s fade-out + 400ms transition + 完全移出 DOM（无 ghost 节点） |
| `tauri-app/src/components/ChatHistoryPanel.tsx` | 新增 | 会话内只读回溯弹窗；`role="dialog"` + `aria-modal` |
| `tauri-app/src/App.tsx` | 修改 | 移除旧 `slice(-5).map` 堆叠 DOM；接入三个新组件；`latestUserInput` / `historyOpen` state；派生 `latestAssistant` |
| `scripts/e2e/drive_via_cdp.py` | 修改 | 新增 `step_dialog_bar`（5 项断言）；`step_text_chat` / `step_esc_interrupt` 从 `chat-bubble-assistant` 迁到 `dialog-bar-assistant` |

## 流程

按 `sp-subagent-driven-development` 执行：每个子任务派独立 implementer → spec compliance reviewer → code quality reviewer，review 红灯再派 fix 子任务循环到绿灯。关键修复：

- **Task 1（E2E 红灯）**：`python-reviewer` 发现 3 处（1 BLOCKER + 2 IMPORTANT）：Playwright ternary 短路误用、`.count()` 守卫缺失、用户气泡出现判定缺重试循环。全部修掉后绿灯。
- **Task 2（DialogBar）**：补 `aria-label`；push back 对 `overflow: hidden + overflow: auto` 的误判（父 `overflow: hidden` 不会破坏子 `overflow: auto` 独立滚动上下文）— reviewer 接受并撤回 BLOCKER。
- **Task 3（UserBubble）**：修掉 ghost-bubble（fade 完后 content → null、节点离开 DOM）；把 `visibleMs` 放 ref 避免误触计时器重启；抽 `FADE_DURATION_MS` 常量让 CSS 过渡和清理计时器对齐。
- **Task 4（ChatHistoryPanel）**：补 dialog a11y（`role="dialog"` / `aria-modal` / 关闭按钮 `aria-label`）。
- **Task 5（App.tsx）**：spec ✅；code quality ✅；`messagesEndRef` 保留 hook 声明但 effect body 清空避免误导未来维护者。

## 验证

**CDP E2E（真 Tauri debug binary + Playwright 9222）**：

```
=== SUMMARY ===
  chat      PASS   （底栏助手文本从上一轮切换到新回复）
  memory    PASS   （84 → 83 轮删除 + 面板开关）
  mic       PASS   （REC 状态双向切换）
  esc       PASS   （Esc 后底栏文字长度 3s 内不再增长）
  dialog    PASS   （5/5 VN 底栏断言）
```

**dialog 五项断言细节**：
1. 底栏 `[data-testid="dialog-bar-assistant"]` 节点数恒为 1
2. 发第二条消息后底栏文本被新回复替换（非堆叠）
3. 用户消息气泡 2.5s 后 `opacity = 0`（或 DOM 已移除）
4. 点 💬 → `[data-testid="chat-history-panel"]` 出现
5. 点 ✕ → 历史面板消失

**类型检查 & 构建**：`npx tsc --noEmit` 0 error；`npx vite build` 通过，471 modules，~0.15s。

## 产物

- `scripts/e2e/shots_cdp/dialog_01_first_reply.png`
- `scripts/e2e/shots_cdp/dialog_02_replaced.png`
- `scripts/e2e/shots_cdp/dialog_03_user_faded.png`
- `scripts/e2e/shots_cdp/dialog_04_history_open.png`
- `scripts/e2e/shots_cdp/dialog_05_history_closed.png`

## 提交序列（master）

| SHA | Subject |
|---|---|
| `4015651` | test(e2e): add dialog bar CDP assertions |
| `215fdae` | fix(e2e): address code-review issues in dialog-bar step |
| `b81bbc7` | feat(ui): add DialogBar component for VN-style bottom dialog |
| `d833d42` | fix(ui): address review feedback on DialogBar |
| `82ac6a5` | feat(ui): add UserBubble fleeting bubble above input |
| `8441afa` | fix(ui): address review feedback on UserBubble |
| `2bb65eb` | feat(ui): add ChatHistoryPanel for current-session readback |
| `f486b86` | fix(ui): add a11y attributes to ChatHistoryPanel |
| `0dd825e` | feat(ui): replace stacked bubbles with VN-style dialog bar |
| `3e65db1` | refactor(e2e): migrate chat/esc steps from chat-bubble-assistant to dialog-bar-assistant |

## 结论

Live2D 人物不再被聊天 DOM 遮挡；用户对话体验改为 VN 风格（单条主对话 + 用户气泡确认 + 历史回溯）。五条 E2E 用户流程（文字对话 / 记忆 / 麦克风 / Esc 中断 / 新 VN 底栏）全部在真桌面窗口里自动化通过。

VN Dialog Bar 验收门：**PASSED**。
