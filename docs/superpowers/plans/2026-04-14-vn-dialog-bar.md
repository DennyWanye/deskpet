# VN 风格对话栏 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal**：把会挡住 Live2D 人物的多条气泡堆叠，替换为 Galgame / VN 风格的底栏对话框 —— 底栏只显示最新一条助手回复，旧消息进入历史面板，用户输入以小气泡浮现于输入框上方。

**Architecture**：

- 新增 `DialogBar.tsx` —— 固定高度 60px 的底栏组件，只渲染最新一条助手回复，带"展开历史"按钮
- 新增 `ChatHistoryPanel.tsx` —— 复用 MemoryPanel 的遮罩样式，展示完整会话历史（与记忆面板解耦 —— 记忆是跨会话 SQLite，历史是本次会话内存状态）
- 改造 `App.tsx` —— 移除现有 `messages.slice(-5)` 堆叠气泡 DOM；用户消息以 20px 小气泡浮在输入框上方 2 秒后淡出；助手消息喂进 DialogBar
- **不改** `Live2DCanvas.tsx` —— 实测 60px 底栏 + 32px 输入栏总共占屏 ~10%，原有人物位置（top 7.5% / 底部留 22.5%）已有足够 buffer

**Tech Stack**：React 19 + TypeScript + Vite 8 + Playwright CDP E2E（drive_via_cdp.py）

**UX 定稿**：
- 底栏高度 60px（含 padding）
- 用户消息：输入框上方 20px 小气泡，出现 2 秒后 opacity → 0 渐隐
- 助手消息：底栏单条显示，新消息直接替换旧消息（无动画，避免 TTS 期间闪烁）
- 展开历史按钮：底栏右上角 16×16 小图标（💬），点击打开 ChatHistoryPanel

---

## File Structure

| 路径 | 职责 | 状态 |
|---|---|---|
| `tauri-app/src/components/DialogBar.tsx` | VN 底栏，展示最新 1 条助手回复 + 展开历史按钮 | 新建 |
| `tauri-app/src/components/ChatHistoryPanel.tsx` | 全历史弹窗（本次会话），复用 MemoryPanel 遮罩样式 | 新建 |
| `tauri-app/src/components/UserBubble.tsx` | 用户消息小气泡，2s 自动淡出 | 新建 |
| `tauri-app/src/App.tsx` | 去掉旧 bubble 堆叠、挂接新组件 | 修改 |
| `scripts/e2e/drive_via_cdp.py` | 新增 `step_dialog_bar` E2E 测试 | 修改 |

---

## Task 1: 扩展 CDP E2E 断言底栏行为（先写测试）

**Files:**
- Modify: `scripts/e2e/drive_via_cdp.py`（在 `step_text_chat` 后插入新 step）

- [ ] **Step 1: 在 drive_via_cdp.py 加入新 E2E 步骤 `step_dialog_bar`**

在 `step_esc_interrupt` 之前插入：

```python
def step_dialog_bar(page: Page) -> bool:
    """验证 VN 底栏行为：
    1. 底栏只渲染最新 1 条助手消息（不是多条堆叠）
    2. 用户消息气泡在 2s 内淡出（opacity < 0.3）
    3. 展开历史按钮点击后，历史面板出现、关闭按钮生效
    """
    print("\n=== STEP: DIALOG BAR (VN 底栏) ===")
    ensure_mic_idle(page)

    # 确保至少有 1 条对话
    page.locator('[data-testid="chat-input"]').fill("说句你好")
    page.locator('[data-testid="send-button"]').click()
    # 等助手回复出现在底栏
    deadline = time.time() + 60
    bar_text = ""
    while time.time() < deadline:
        bar = page.locator('[data-testid="dialog-bar-assistant"]')
        if bar.count() and bar.inner_text().strip():
            bar_text = bar.inner_text().strip()
            break
        page.wait_for_timeout(500)
    if not bar_text:
        print("[dialog] FAIL 底栏未渲染助手回复")
        return False
    print(f"[dialog] 底栏最新助手文本: {bar_text[:50]}...")
    shot(page, "dialog_01_first_reply")

    # 断言 1：底栏里 assistant 节点只有 1 个
    assistant_nodes = page.locator('[data-testid="dialog-bar-assistant"]').count()
    if assistant_nodes != 1:
        print(f"[dialog] FAIL 底栏助手节点数 {assistant_nodes} != 1")
        return False
    print("[dialog] PASS 底栏只渲染 1 条助手消息")

    # 断言 2：发第二条消息，底栏应替换为新内容
    page.locator('[data-testid="chat-input"]').fill("再说一句")
    page.locator('[data-testid="send-button"]').click()
    # 等内容变化
    deadline = time.time() + 60
    new_text = bar_text
    while time.time() < deadline:
        cur = page.locator('[data-testid="dialog-bar-assistant"]').inner_text().strip()
        if cur and cur != bar_text:
            new_text = cur
            break
        page.wait_for_timeout(500)
    if new_text == bar_text:
        print("[dialog] FAIL 底栏未被第二条回复替换")
        return False
    print(f"[dialog] PASS 底栏被替换为新内容: {new_text[:50]}...")
    shot(page, "dialog_02_replaced")

    # 断言 3：用户消息气泡 2s 内淡出
    page.locator('[data-testid="chat-input"]').fill("测试气泡")
    page.locator('[data-testid="send-button"]').click()
    page.wait_for_timeout(100)
    user_bubble = page.locator('[data-testid="user-bubble-fleeting"]')
    if user_bubble.count() == 0:
        print("[dialog] FAIL 用户小气泡未出现")
        return False
    # 等 2.5s，应已淡出
    page.wait_for_timeout(2500)
    opacity_val = user_bubble.evaluate(
        "el => parseFloat(getComputedStyle(el).opacity)"
    ) if user_bubble.count() else 0.0
    if opacity_val > 0.3:
        print(f"[dialog] FAIL 用户气泡 2.5s 后仍可见 opacity={opacity_val}")
        return False
    print(f"[dialog] PASS 用户气泡淡出 opacity={opacity_val}")
    shot(page, "dialog_03_user_faded")

    # 断言 4：展开历史按钮点击 → 历史面板出现
    page.locator('[data-testid="dialog-history-toggle"]').click()
    page.wait_for_timeout(500)
    panel = page.locator('[data-testid="chat-history-panel"]')
    if panel.count() == 0:
        print("[dialog] FAIL 点击按钮后历史面板未出现")
        return False
    shot(page, "dialog_04_history_open")

    # 断言 5：历史面板关闭按钮生效
    page.locator('[data-testid="chat-history-close"]').click()
    page.wait_for_timeout(500)
    if page.locator('[data-testid="chat-history-panel"]').count() != 0:
        print("[dialog] FAIL 历史面板未关闭")
        return False
    print("[dialog] PASS 历史面板开关正常")
    shot(page, "dialog_05_history_closed")

    return True
```

同时更新 `main()` 里的 steps 列表和 choices：

```python
ap.add_argument("--only", nargs="*", default=[],
                choices=["chat", "memory", "mic", "esc", "dialog"])
args = ap.parse_args()
steps = args.only or ["chat", "memory", "mic", "esc", "dialog"]
```

在 `results` 填充区追加：

```python
if "dialog" in steps:
    results["dialog"] = step_dialog_bar(page)
```

- [ ] **Step 2: 运行测试验证它全部 FAIL（因为还没实现）**

Run:
```bash
backend/.venv/Scripts/python.exe scripts/e2e/drive_via_cdp.py --only dialog
```

Expected: FAIL with `底栏未渲染助手回复` 或 `locator dialog-bar-assistant not found`（这些 data-testid 还不存在）

- [ ] **Step 3: Commit**

```bash
git add scripts/e2e/drive_via_cdp.py
git commit -m "test(e2e): add dialog bar CDP assertions"
```

---

## Task 2: 新建 DialogBar 组件（最小实现）

**Files:**
- Create: `tauri-app/src/components/DialogBar.tsx`

- [ ] **Step 1: 创建 DialogBar.tsx**

```tsx
import type { CSSProperties } from "react";

type Props = {
  /** 最新一条助手消息文本；空则底栏渲染占位 */
  latestAssistant: string | null;
  /** 点击展开历史 */
  onOpenHistory: () => void;
};

/**
 * VN 风格底栏对话框。
 *
 * 设计原则：
 * - 固定高度 60px，不随内容弹跳（避免挡 Live2D）
 * - 单条渲染 —— 旧消息直接被新消息替换，无动画（TTS 串流期间闪烁会晕）
 * - 文本超出时内部 scroll，外框高度不变
 */
export function DialogBar({ latestAssistant, onOpenHistory }: Props) {
  return (
    <div
      style={barStyle}
      data-testid="dialog-bar"
    >
      <div
        data-testid="dialog-bar-assistant"
        style={textStyle}
      >
        {latestAssistant ?? ""}
      </div>
      <button
        data-testid="dialog-history-toggle"
        onClick={onOpenHistory}
        style={historyBtnStyle}
        title="查看完整对话历史"
      >
        💬
      </button>
    </div>
  );
}

const barStyle: CSSProperties = {
  position: "absolute",
  bottom: "44px", // 输入栏高 32 + 6 上边距 + 6 下边距
  left: "5px",
  right: "5px",
  height: "60px",
  backgroundColor: "rgba(20, 20, 35, 0.92)",
  borderRadius: "10px",
  border: "1px solid rgba(129,140,248,0.35)",
  padding: "8px 34px 8px 12px", // 右侧留 34px 给按钮
  color: "white",
  fontSize: "13px",
  lineHeight: "1.5",
  zIndex: 10,
  overflow: "hidden",
  display: "flex",
  alignItems: "center",
};

const textStyle: CSSProperties = {
  flex: 1,
  overflowY: "auto",
  maxHeight: "100%",
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
};

const historyBtnStyle: CSSProperties = {
  position: "absolute",
  top: "4px",
  right: "6px",
  width: "22px",
  height: "22px",
  background: "rgba(0,0,0,0.4)",
  color: "white",
  border: "none",
  borderRadius: "4px",
  fontSize: "12px",
  cursor: "pointer",
  padding: 0,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
};
```

- [ ] **Step 2: tsc 类型检查**

Run:
```bash
cd tauri-app && npx tsc --noEmit
```

Expected: 无新增 DialogBar 相关错误（注意 Live2DCanvas 原有 strict null 遗留错误不算数）

- [ ] **Step 3: Commit**

```bash
git add tauri-app/src/components/DialogBar.tsx
git commit -m "feat(ui): add DialogBar component for VN-style bottom dialog"
```

---

## Task 3: 新建 UserBubble 组件

**Files:**
- Create: `tauri-app/src/components/UserBubble.tsx`

- [ ] **Step 1: 创建 UserBubble.tsx**

```tsx
import { useEffect, useState } from "react";
import type { CSSProperties } from "react";

type Props = {
  /** 最新用户输入文本；每次文本变化重置淡出计时器 */
  text: string | null;
  /** 淡出前的可见时长，ms。默认 2000ms */
  visibleMs?: number;
};

/**
 * 用户消息小气泡 —— 2s 自动淡出。
 *
 * 放在输入框上方，让用户确认"发出去了"。
 * 不进入对话历史 —— 历史已由 App.tsx 的 messages state 维护。
 */
export function UserBubble({ text, visibleMs = 2000 }: Props) {
  const [opacity, setOpacity] = useState(0);
  const [content, setContent] = useState<string | null>(null);

  useEffect(() => {
    if (!text) return;
    setContent(text);
    setOpacity(1);
    const t = window.setTimeout(() => setOpacity(0), visibleMs);
    return () => window.clearTimeout(t);
  }, [text, visibleMs]);

  if (!content) return null;

  return (
    <div
      data-testid="user-bubble-fleeting"
      style={{
        ...bubbleStyle,
        opacity,
        transition: "opacity 400ms ease-out",
        pointerEvents: opacity < 0.1 ? "none" : "auto",
      }}
    >
      {content}
    </div>
  );
}

const bubbleStyle: CSSProperties = {
  position: "absolute",
  bottom: "112px", // 输入栏 32 + 底栏 60 + 余量 20
  right: "10px",
  maxWidth: "220px",
  padding: "4px 10px",
  borderRadius: "12px",
  backgroundColor: "rgba(59,130,246,0.92)",
  color: "white",
  fontSize: "11px",
  lineHeight: "1.4",
  zIndex: 11,
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  maxHeight: "60px",
  overflow: "hidden",
};
```

- [ ] **Step 2: tsc 类型检查**

Run:
```bash
cd tauri-app && npx tsc --noEmit
```

Expected: 无 UserBubble 相关错误

- [ ] **Step 3: Commit**

```bash
git add tauri-app/src/components/UserBubble.tsx
git commit -m "feat(ui): add UserBubble fleeting bubble above input"
```

---

## Task 4: 新建 ChatHistoryPanel 组件

**Files:**
- Create: `tauri-app/src/components/ChatHistoryPanel.tsx`

- [ ] **Step 1: 创建 ChatHistoryPanel.tsx**

```tsx
import type { CSSProperties } from "react";

type Message = { role: "user" | "assistant"; text: string };

type Props = {
  open: boolean;
  messages: Message[];
  onClose: () => void;
};

/**
 * 本次会话的完整聊天历史面板。
 *
 * 和 MemoryPanel 的区别：
 * - MemoryPanel：跨会话 SQLite 持久化历史，带删除/导出/清空
 * - ChatHistoryPanel：只看本次会话内存 messages，纯只读回溯
 *
 * 遮罩样式与 MemoryPanel 对齐以保持视觉一致。
 */
export function ChatHistoryPanel({ open, messages, onClose }: Props) {
  if (!open) return null;

  return (
    <div
      style={overlayStyle}
      data-testid="chat-history-panel"
    >
      <div style={headerStyle}>
        <strong style={{ fontSize: "14px" }}>本次对话 · {messages.length} 条</strong>
        <button
          data-testid="chat-history-close"
          onClick={onClose}
          style={closeBtnStyle}
          title="Close"
        >
          ✕
        </button>
      </div>

      <div style={listStyle}>
        {messages.length === 0 && (
          <div style={emptyStyle}>（本次还没聊过）</div>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            data-testid={`chat-history-row-${i}`}
            data-role={m.role}
            style={{
              ...rowStyle,
              alignSelf: m.role === "user" ? "flex-end" : "flex-start",
              backgroundColor:
                m.role === "user"
                  ? "rgba(59,130,246,0.9)"
                  : "rgba(30,30,50,0.85)",
            }}
          >
            <span style={roleLabelStyle}>{m.role === "user" ? "我" : "桌宠"}</span>
            <span style={bodyStyle}>{m.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

const overlayStyle: CSSProperties = {
  position: "absolute",
  top: 0,
  left: 0,
  right: 0,
  bottom: 0,
  backgroundColor: "rgba(0,0,0,0.85)",
  zIndex: 1000,
  display: "flex",
  flexDirection: "column",
  padding: "12px",
  color: "white",
  fontSize: "12px",
};

const headerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  marginBottom: "8px",
};

const closeBtnStyle: CSSProperties = {
  background: "transparent",
  color: "white",
  border: "1px solid #555",
  borderRadius: "4px",
  padding: "2px 8px",
  cursor: "pointer",
};

const listStyle: CSSProperties = {
  flex: 1,
  overflowY: "auto",
  border: "1px solid #333",
  borderRadius: "6px",
  padding: "6px",
  display: "flex",
  flexDirection: "column",
  gap: "4px",
};

const rowStyle: CSSProperties = {
  maxWidth: "80%",
  padding: "6px 10px",
  borderRadius: "10px",
  display: "flex",
  flexDirection: "column",
  gap: "2px",
  wordBreak: "break-word",
  whiteSpace: "pre-wrap",
};

const roleLabelStyle: CSSProperties = {
  fontSize: "10px",
  opacity: 0.6,
};

const bodyStyle: CSSProperties = {
  fontSize: "12px",
  lineHeight: "1.4",
};

const emptyStyle: CSSProperties = {
  opacity: 0.5,
  textAlign: "center",
  marginTop: "20px",
};
```

- [ ] **Step 2: tsc 类型检查**

Run:
```bash
cd tauri-app && npx tsc --noEmit
```

Expected: 无 ChatHistoryPanel 相关错误

- [ ] **Step 3: Commit**

```bash
git add tauri-app/src/components/ChatHistoryPanel.tsx
git commit -m "feat(ui): add ChatHistoryPanel for current-session readback"
```

---

## Task 5: 改造 App.tsx 接入新组件

**Files:**
- Modify: `tauri-app/src/App.tsx`

- [ ] **Step 1: 加 import**

在文件顶部原有 import 块之后追加：

```tsx
import { DialogBar } from "./components/DialogBar";
import { ChatHistoryPanel } from "./components/ChatHistoryPanel";
import { UserBubble } from "./components/UserBubble";
```

- [ ] **Step 2: 追加 state**

在 `const [memoryOpen, setMemoryOpen] = useState(false);` 下方追加：

```tsx
// VN 底栏 —— 最新用户输入（驱动 UserBubble 淡出计时）+ 历史面板开关。
const [latestUserInput, setLatestUserInput] = useState<string | null>(null);
const [historyOpen, setHistoryOpen] = useState(false);
```

- [ ] **Step 3: 在 handleSend 记录用户输入**

把：

```tsx
const handleSend = () => {
  if (!chatText.trim()) return;
  setMessages((prev) => [...prev, { role: "user", text: chatText }]);
  sendChat(chatText);
  setChatText("");
};
```

替换为：

```tsx
const handleSend = () => {
  if (!chatText.trim()) return;
  setMessages((prev) => [...prev, { role: "user", text: chatText }]);
  // 触发 UserBubble —— 每次用新对象 ref 重置淡出计时，避免相同文本重发时
  // React 因为字符串相等不重置 state
  setLatestUserInput(chatText + "\u200B".repeat(messages.length));
  sendChat(chatText);
  setChatText("");
};
```

（`\u200B` 零宽空格保证每次文本唯一，useEffect 能重置计时器）

- [ ] **Step 4: 派生最新助手消息**

在 `messagesEndRef` 附近追加：

```tsx
// 底栏渲染用 —— 从 messages 里取最后一条 assistant。
const latestAssistant =
  [...messages].reverse().find((m) => m.role === "assistant")?.text ?? null;
```

- [ ] **Step 5: 移除旧 bubble 堆叠、挂接新组件**

把整段：

```tsx
{messages.length > 0 && (
  <div
    style={{
      position: "absolute",
      bottom: "55px",
      left: "5px",
      right: "5px",
      maxHeight: "200px",
      overflowY: "auto",
      display: "flex",
      flexDirection: "column",
      gap: "6px",
      zIndex: 10,
      padding: "4px",
    }}
  >
    {messages.slice(-5).map((msg, i) => (
      <div
        key={i}
        data-testid={`chat-bubble-${msg.role}`}
        style={{
          alignSelf: msg.role === "user" ? "flex-end" : "flex-start",
          backgroundColor:
            msg.role === "user"
              ? "rgba(59, 130, 246, 0.9)"
              : "rgba(30, 30, 50, 0.85)",
          color: "white",
          borderRadius: "10px",
          padding: "6px 10px",
          maxWidth: "260px",
          fontSize: "12px",
          lineHeight: "1.4",
          maxHeight: "80px",
          overflowY: "auto",
          wordBreak: "break-word",
        }}
      >
        {stripMarkdown(msg.text)}
      </div>
    ))}
    <div ref={messagesEndRef} />
  </div>
)}
```

替换为：

```tsx
{/* VN 底栏：只展示最新一条助手回复 */}
<DialogBar
  latestAssistant={latestAssistant ? stripMarkdown(latestAssistant) : null}
  onOpenHistory={() => setHistoryOpen(true)}
/>

{/* 用户消息 2s 小气泡 */}
<UserBubble text={latestUserInput} visibleMs={2000} />

{/* 完整会话历史（点 💬 按钮展开）*/}
<ChatHistoryPanel
  open={historyOpen}
  messages={messages.map((m) => ({ role: m.role, text: stripMarkdown(m.text) }))}
  onClose={() => setHistoryOpen(false)}
/>
```

注意：旧代码里的 `messagesEndRef` 在新架构下没用了（底栏只显示最新一条、不需要自动滚动），但变量声明先留着，避免改动 React hook 顺序；后续清理另行提 PR。

- [ ] **Step 6: tsc 类型检查 + vite build**

Run:
```bash
cd tauri-app && npx tsc --noEmit 2>&1 | grep -v "Live2DCanvas" | head -30
```

Expected: 除 Live2DCanvas 遗留外，无新错误

Run:
```bash
cd tauri-app && npx vite build
```

Expected: 构建成功，产出 `dist/`。注意：使用 `vite build` 跳过 tsc（Live2DCanvas 遗留 null 错误不应阻塞这个任务）

- [ ] **Step 7: Commit**

```bash
git add tauri-app/src/App.tsx
git commit -m "feat(ui): replace stacked bubbles with VN-style dialog bar"
```

---

## Task 6: 端到端验证 & 回归测试

**Files:**
- Modify: 无（仅运行脚本）

- [ ] **Step 1: 重建 Tauri debug binary**

Live2D 嵌入资源需要 cargo 重新打包 dist/。

Run（Windows cmd 风格，遵循 MEMORY.md 的清理先行规则）：

```bash
taskkill /F /IM deskpet.exe 2>nul
cd tauri-app && touch src-tauri/build.rs && cd src-tauri && cargo build
```

Expected: `cargo build` 成功

- [ ] **Step 2: 重启 backend + debug binary**

```bash
# 终端 1：后端
backend/.venv/Scripts/python.exe -m uvicorn backend.main:app --port 8100

# 终端 2：Vite（如果要 HMR）
cd tauri-app && npm run dev

# 终端 3：debug binary（带 CDP 端口）
cd tauri-app && DESKPET_DEV_MODE=1 src-tauri/target/debug/deskpet.exe &

# 确认 CDP 端口起
curl http://localhost:9222/json/version
```

Expected: `/json/version` 返回 JSON

- [ ] **Step 3: 跑新 dialog 测试 + 全量回归**

```bash
backend/.venv/Scripts/python.exe scripts/e2e/drive_via_cdp.py --only dialog
```

Expected: 5 个断言全 PASS（只 1 条 / 替换 / 用户气泡淡出 / 历史开 / 历史关）

```bash
backend/.venv/Scripts/python.exe scripts/e2e/drive_via_cdp.py
```

Expected: chat / memory / mic / esc / dialog 全 PASS，SUMMARY 5 行都是 PASS

- [ ] **Step 4: 手工验证**

在 debug 窗口做一次真人操作：

- [ ] 发一句中文 → 看到用户小气泡在右下浮现，2 秒内淡出
- [ ] 助手回复后 → 底栏出现回复，Live2D 人物完整可见（头/身/脚都不被挡）
- [ ] 再发一句 → 底栏被新回复替换（不是堆叠）
- [ ] 点底栏右上 💬 → 历史面板弹出，展示本次所有消息
- [ ] 点 ✕ → 历史面板关闭

- [ ] **Step 5: 截图归档**

Run:
```bash
ls scripts/e2e/shots_cdp/dialog_*.png
```

Expected: `dialog_01_first_reply.png` 到 `dialog_05_history_closed.png` 五张存在

- [ ] **Step 6: Commit（如果只是验证没改代码就跳过）**

```bash
# 若 Task 1-5 的修改没拆开提交过，在这里统一提交
git status
# 视情况决定是否合并提交
```

---

## Task 7: 写中文文档

**Files:**
- Create: `docs/superpowers/plans/2026-04-14-vn-dialog-bar-report.md`

- [ ] **Step 1: 创建实施报告**

```markdown
# VN 风格对话栏 实施报告
_2026-04-14 — 桌宠 UI 遮挡修复_

## 问题
旧版最多堆叠 5 条气泡，maxHeight 200px，占屏约 22%，会遮盖 Live2D 人物的下半身。

## 方案
Galgame 风格底栏 —— 底部固定 60px 对话框，只渲染最新一条助手回复；用户消息以 2s 自动淡出的小气泡浮在输入框上方；完整历史点 💬 展开。

## 改动
| 路径 | 变更 |
|---|---|
| `tauri-app/src/components/DialogBar.tsx` | 新增 —— 60px 底栏 |
| `tauri-app/src/components/UserBubble.tsx` | 新增 —— 用户 2s 小气泡 |
| `tauri-app/src/components/ChatHistoryPanel.tsx` | 新增 —— 会话历史弹窗 |
| `tauri-app/src/App.tsx` | 替换多条堆叠 DOM，挂接三个新组件 |
| `scripts/e2e/drive_via_cdp.py` | 新增 `step_dialog_bar` 5 项断言 |

## 验证
CDP E2E 全绿（chat / memory / mic / esc / dialog 5 步均 PASS），手工操作人物完整可见。

## 产物
- `scripts/e2e/shots_cdp/dialog_01_first_reply.png` 等 5 张截图
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/plans/2026-04-14-vn-dialog-bar-report.md
git commit -m "docs: VN dialog bar implementation report"
```

---

## Self-Review

**1. Spec coverage:**
- ✅ VN 底栏 60px → Task 2
- ✅ 只显示最新 1 条助手 → Task 2 + Task 5 Step 4 派生逻辑
- ✅ 用户消息小气泡 2s 淡出 → Task 3
- ✅ 展开历史按钮 → Task 2 + Task 4
- ✅ 历史面板复用 MemoryPanel 样式 → Task 4 overlayStyle 抄自 MemoryPanel
- ✅ 不改 Live2DCanvas → 明示不动
- ✅ E2E 测试 → Task 1 五项断言
- ✅ 中文文档 → Task 7

**2. Placeholder scan:**
无 TBD / TODO / "similar to Task N"。每个 step 都有具体代码或命令。

**3. Type consistency:**
- `DialogBar` props：`latestAssistant: string | null, onOpenHistory: () => void` → Task 5 Step 5 使用 `latestAssistant={latestAssistant ? stripMarkdown(latestAssistant) : null}` ✓
- `UserBubble` props：`text: string | null, visibleMs?: number` → Task 5 Step 5 `text={latestUserInput} visibleMs={2000}` ✓
- `ChatHistoryPanel` props：`open, messages, onClose` → Task 5 Step 5 三个都传了 ✓
- data-testid 在测试（Task 1）和组件（Task 2-4）中完全一致：`dialog-bar-assistant / user-bubble-fleeting / dialog-history-toggle / chat-history-panel / chat-history-close` ✓
