import { useEffect, useRef, type CSSProperties } from "react";

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
 *
 * 无障碍（P2-0-S6）：
 * - 面板打开时关闭按钮自动 focus（屏幕阅读器能立即读到对话框 label）
 * - Tab / Shift+Tab 在可聚焦元素内循环（简易 focus trap，防止焦点跑到
 *   背后的 chat input）
 * - Escape 键关闭面板（等价于点 ✕）
 */
export function ChatHistoryPanel({ open, messages, onClose }: Props) {
  const panelRef = useRef<HTMLDivElement>(null);
  const closeBtnRef = useRef<HTMLButtonElement>(null);

  // 面板打开时把焦点挪到关闭按钮 —— 读屏软件在此时会朗读 aria-label
  // "本次对话历史"，然后朗读按钮本身。关闭时把焦点还给 document.body 即可，
  // 调用方（App.tsx）负责把焦点放回原触发按钮（dialog-history-toggle）。
  useEffect(() => {
    if (!open) return;
    closeBtnRef.current?.focus();
  }, [open]);

  // Escape 关闭 + Tab 循环焦点。只在 open 时挂载 listener，避免误吃键。
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      // 简易 focus trap：拦住 Tab / Shift+Tab，让焦点在面板内的可聚焦
      // 元素间循环。对只有一个交互元素（✕ 按钮）的场景也安全 —— first
      // 和 last 是同一个，Tab / Shift+Tab 都只是 preventDefault + 继续聚焦它。
      const root = panelRef.current;
      if (!root) return;
      const focusables = root.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement as HTMLElement | null;
      if (e.shiftKey) {
        if (active === first || !root.contains(active)) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (active === last || !root.contains(active)) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      ref={panelRef}
      style={overlayStyle}
      data-testid="chat-history-panel"
      role="dialog"
      aria-modal="true"
      aria-label="本次对话历史"
    >
      <div style={headerStyle}>
        <strong style={{ fontSize: "14px" }}>本次对话 · {messages.length} 条</strong>
        <button
          ref={closeBtnRef}
          data-testid="chat-history-close"
          onClick={onClose}
          style={closeBtnStyle}
          title="Close"
          aria-label="关闭对话历史"
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
