# UI-1 — 聊天气泡覆盖方案 E（头顶轻气泡 + 展开历史面板）

**日期：** 2026-04-14
**分支：** `feat/ui-1-chat-overlay`
**前置：** Phase 1 已交付（`v0.1.0-phase1`）、CDP E2E 已就位

---

## 1. 背景 & 问题

当前窗口 400×600 CSS 下：

```
y=0   ┌──────────────────────┐
      │ 状态栏（🗂⏻FPS 状态）  │
y=150 │                      │
      │  Live2D 头部（露出）    │
y=345 │ ━━━━━━━━━━━━━━━━━━ │ ← 气泡顶部
      │ 💬 消息 1              │
      │ 💬 消息 2              │ ← 遮挡角色下半身
      │ 💬 消息 3              │
y=545 │ ━━━━━━━━━━━━━━━━━━ │ ← 气泡底部
      │ 🎤 [输入] [发送]        │
y=600 └──────────────────────┘
```

**问题**：消息列表 `position: absolute; bottom: 55px; maxHeight: 200px` 正好盖在 Live2D 角色 y=345–545 范围上，用户看不到角色身体。这严重破坏桌宠"有生命感"的核心体验。

---

## 2. 目标 — 方案 E（混合模式）

### 常态（99% 时间）

```
        ╔══════════╗
        ║ 我是助手… ║ ← 只显示最新一条助手气泡，头顶悬浮
        ╚═══╗──────╝      5 秒后渐隐（用户新发消息时重置计时）
            ▼
        ┌──────┐
        │ 🐱 头 │
        │ 身体  │ ← 角色完全露出
        │      │
        └──────┘
        📜 🎤 [输入] [发送]    ← 新增 📜 按钮
```

### 点 📜 按钮后 —— 展开历史面板

```
    ┌─────────────────────┐
    │ 💬 对话历史       ✕  │
    │ ─────────────────── │
    │ user: 你好           │ ← 盖在角色上，复用 MemoryPanel 风格
    │ assistant: 你好！    │
    │ user: 今天天气？      │
    │ assistant: 晴天…     │
    │ ─────────────────── │
    │ [清空] [导出]         │
    └─────────────────────┘
```

---

## 3. 范围

### ✅ 范围内

- **新建 `ChatBubble.tsx`**：头顶悬浮气泡组件
  - 只渲染最新一条 assistant 消息
  - 5 秒后自动渐隐（CSS transition）
  - 新消息到来重置计时
  - 点击气泡触发"保持常驻"（可选 P1，不做进 P0）
- **新建 `ChatHistoryPanel.tsx`**：历史对话面板
  - UI 风格完全复用现有 `MemoryPanel.tsx`（保持视觉一致性）
  - 展示所有历史消息（不限 slice(-5)）
  - 不做持久化（前端 in-memory messages 即可，历史持久化已由 MemoryPanel 覆盖）
- **改 `App.tsx`**：
  - 删除现有盖住角色的消息列表渲染块（266–308 行）
  - 接入 `<ChatBubble />`（接最新 assistant 消息）
  - 状态栏加 📜 按钮，切换 `chatHistoryOpen` state
  - 条件渲染 `<ChatHistoryPanel />`
- **CDP E2E 用例扩**：
  - 新增 `step_chat_overlay`：验证气泡出现、5 秒后淡出、点 📜 能打开面板

### ❌ 非范围

- 气泡拖动 / 自定义位置
- 气泡尾巴指向嘴（Live2D 坐标跟随，工程量大，留给 Phase 2）
- 用户气泡 / assistant 气泡不同样式（只渲染最新 assistant）
- 历史面板的"点击消息跳转 / 引用"功能
- 气泡 markdown 渲染增强（保留现有 `stripMarkdown`）
- 键盘快捷键（Ctrl+H 打开历史等）— P1 再说

---

## 4. 设计要点

### 4.1 ChatBubble 组件契约

```tsx
interface ChatBubbleProps {
  latestAssistantMessage: string | null;  // null = 不渲染
  autoHideMs?: number;                    // 默认 5000
}
```

**行为**：
- `latestAssistantMessage` 变化 → 重置 visible=true、重启 setTimeout
- `autoHideMs` 后 visible=false，CSS opacity 渐隐 300ms
- 鼠标悬停气泡时取消计时（UX 甜点，不 P0 必须）

**样式定位**：
- `position: absolute; top: 40px; left: 50%; transform: translateX(-50%)`
- `max-width: 320px; max-height: 80px; overflow: auto`
- 背景 `rgba(30, 30, 50, 0.88)` + 白字 + 圆角 12px
- 下方 ▼ 尾巴（纯 CSS border 三角）指向角色头部方向

**data-testid**：`chat-bubble-overlay`（新 testid，和现有 `chat-bubble-assistant` 区分）

### 4.2 ChatHistoryPanel 组件契约

```tsx
interface ChatHistoryPanelProps {
  open: boolean;
  onClose: () => void;
  messages: { role: "user" | "assistant"; text: string }[];
}
```

**UI**：
- 直接复用 `MemoryPanel.tsx` 的外层结构（full-overlay + 顶部标题栏 + ✕ 按钮 + 滚动列表）
- 不需要和后端对话（messages 直接从 App.tsx props 传入）
- 空态显示 "暂无对话记录"
- 不含"清空/导出"按钮（那是 MemoryPanel 的职责，这里只是展示）

**data-testid**：
- 面板容器：`chat-history-panel`
- 关闭按钮：`chat-history-close`
- 每条消息：`chat-history-message-${index}`

### 4.3 App.tsx 改动摘要

```diff
+ const [chatHistoryOpen, setChatHistoryOpen] = useState(false);

+ const latestAssistant = useMemo(
+   () => [...messages].reverse().find(m => m.role === "assistant")?.text ?? null,
+   [messages]
+ );

- {messages.length > 0 && ( /* 当前盖住角色的块，266-308 行，删除 */ )}
+ <ChatBubble latestAssistantMessage={latestAssistant} />

+ {/* 状态栏新增 */}
+ <button data-testid="chat-history-toggle" onClick={() => setChatHistoryOpen(true)}>📜</button>

+ <ChatHistoryPanel
+   open={chatHistoryOpen}
+   onClose={() => setChatHistoryOpen(false)}
+   messages={messages}
+ />
```

---

## 5. 改动文件清单

| 路径 | 类型 | 行数估算 |
|---|---|---|
| `tauri-app/src/components/ChatBubble.tsx` | 新建 | ~60 行 |
| `tauri-app/src/components/ChatHistoryPanel.tsx` | 新建 | ~90 行 |
| `tauri-app/src/App.tsx` | 修改 | +15 / -45 行 |
| `scripts/e2e/drive_via_cdp.py` | 修改 | +40 行（新 step） |

---

## 6. 验收门（5 项，沿用 Phase 1 标准）

- ✅ **E2E 用例通过**：`drive_via_cdp.py --only overlay` 新 step 全 PASS
- ✅ **类型检查通过**：`npx tsc -b` 不能引入新的 TS 错误（Live2DCanvas 的老错误不算回归）
- ✅ **回归测试通过**：现有 chat / memory / mic / esc 4 步 E2E 全 PASS
- ✅ **视觉验证**：手动启动 debug 版，截图确认：
  - (a) 发一条消息后头顶出现气泡
  - (b) 5 秒后气泡自动消失
  - (c) 消失后 Live2D 角色从头到脚完全露出
  - (d) 点 📜 弹出历史面板，显示所有消息
  - (e) ✕ 关闭面板，回到常态
- ✅ **中文文档**：本 plan + 完成后写 handoff 文档

---

## 7. 实现步骤（串行，单 Agent）

### Step 1 — 基础组件（0.5 天）
1. 新建 `ChatBubble.tsx`，独立渲染、带 Storybook-style 本地测试
2. 新建 `ChatHistoryPanel.tsx`，参考 MemoryPanel 抽样式
3. 跑 `npx tsc -b`，两个新文件必须零类型错误

### Step 2 — 接入 App（0.5 天）
1. 改 App.tsx：移除旧消息块，接入新组件
2. 加 📜 toggle 按钮到状态栏（和 🗂 同风格）
3. 跑 Vite dev + Tauri debug，手动点一遍确认效果

### Step 3 — E2E 覆盖（0.5 天）
1. 在 `drive_via_cdp.py` 加 `step_chat_overlay()`
2. 验证流程：发消息 → 见气泡 → 等 6s → 气泡消失 → 截图确认角色可见 → 点 📜 → 见面板 → 点 ✕ → 返回
3. 跑全套 E2E 确认无回归

### Step 4 — 打包验证 + 文档（0.5 天）
1. `npx vite build && touch src-tauri/build.rs && cargo -C src-tauri build`
2. 装 release 版走一遍确认生产构建 OK
3. 写 handoff 文档 `docs/superpowers/plans/2026-04-14-slice-ui1-handoff.md`

**总工程量：2 天**（方案 E 预估 1.5-2 天，吻合）

---

## 8. 风险 & 兜底

| 风险 | 概率 | 兜底 |
|---|---|---|
| CSS transform 让气泡位置在不同窗口尺寸下错位 | 中 | 用 flexbox + % 定位代替 px，窗口 resize 时自适应 |
| Live2D 画布 repaint 导致气泡被盖 | 低 | 气泡 z-index 设 100，Live2D canvas 本身 z-index 默认 0 |
| 5 秒自动消失太短（用户还没读完） | 中 | 动态根据字数估算（80 字/秒 中文阅读速度），min 3s max 10s |
| 历史面板和 MemoryPanel 两者用户混淆 | 中 | 📜 = 当前会话（前端内存）；🗂 = 持久化历史（SQLite） — 在 title 文字上明确区分 |
| E2E 里的 5 秒 timeout 跨机器不稳定 | 中 | E2E 加 2 秒 buffer（等 7 秒验证已消失），或用 `page.wait_for_selector(state="hidden")` |

---

## 9. 不在这个 slice 做的（下个 slice 候选）

- 气泡尾巴自动指向 Live2D 角色嘴部坐标
- 气泡支持图片 / 链接预览
- 用户自定义气泡位置（拖拽）
- Ctrl+H 快捷键打开历史
- 最近 N 条（2-3 条）同时显示的模式切换

---

## 10. 交付物

- 2 个新 React 组件 + 1 个 App.tsx 改造
- 1 个新 E2E 步骤
- 本 plan + handoff 文档
- 视觉 before/after 截图对比
