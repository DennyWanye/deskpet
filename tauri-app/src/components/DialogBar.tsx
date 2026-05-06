/**
 * VN-style dialog bar — P4-S20-UI revamp.
 *
 * 设计：
 * - 玻璃质地（深色半透明 + blur）
 * - 助手回复用 13.5px / 1.55 line-height，柔和留白
 * - 空态文案右下用极弱的提示色（不和真实回复争夺视觉注意）
 * - 历史按钮悬浮态高亮
 */
import type { CSSProperties } from "react";

type Props = {
  /** 最新一条助手消息文本；空则显示 placeholder 引导用户说话 */
  latestAssistant: string | null;
  /** 点击展开历史 */
  onOpenHistory: () => void;
};

const EMPTY_PLACEHOLDER = "按住下方按钮说话，或输入消息开始聊天…";

export function DialogBar({ latestAssistant, onOpenHistory }: Props) {
  const isEmpty = latestAssistant === null || latestAssistant === "";
  return (
    <div style={barStyle} data-testid="dialog-bar">
      <div
        data-testid="dialog-bar-assistant"
        data-empty={isEmpty ? "true" : "false"}
        style={isEmpty ? { ...textStyle, ...placeholderStyle } : textStyle}
      >
        {isEmpty ? EMPTY_PLACEHOLDER : latestAssistant}
      </div>
      <button
        data-testid="dialog-history-toggle"
        onClick={onOpenHistory}
        style={historyBtnStyle}
        title="查看完整对话历史"
        aria-label="查看完整对话历史"
        onMouseEnter={(e) => {
          (e.currentTarget as HTMLButtonElement).style.background =
            "rgba(255, 255, 255, 0.16)";
        }}
        onMouseLeave={(e) => {
          (e.currentTarget as HTMLButtonElement).style.background =
            "rgba(255, 255, 255, 0.06)";
        }}
      >
        💬
      </button>
    </div>
  );
}

const barStyle: CSSProperties = {
  position: "absolute",
  bottom: 56,
  left: 8,
  right: 8,
  minHeight: 64,
  maxHeight: 96,
  background: "rgba(15, 18, 28, 0.85)",
  borderRadius: 14,
  border: "1px solid rgba(148, 163, 184, 0.14)",
  padding: "10px 44px 10px 14px",
  color: "#e2e8f0",
  fontSize: 13.5,
  lineHeight: 1.55,
  zIndex: 10,
  overflow: "hidden",
  display: "flex",
  alignItems: "center",
  backdropFilter: "blur(14px)",
  boxShadow: "0 4px 24px rgba(0, 0, 0, 0.18)",
};

const textStyle: CSSProperties = {
  flex: 1,
  overflowY: "auto",
  maxHeight: "100%",
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  letterSpacing: 0.1,
};

const placeholderStyle: CSSProperties = {
  opacity: 0.55,
  fontStyle: "italic",
  color: "#94a3b8",
};

const historyBtnStyle: CSSProperties = {
  position: "absolute",
  top: 8,
  right: 8,
  width: 28,
  height: 28,
  background: "rgba(255, 255, 255, 0.06)",
  color: "#e2e8f0",
  border: "1px solid rgba(148, 163, 184, 0.14)",
  borderRadius: 8,
  fontSize: 13,
  cursor: "pointer",
  padding: 0,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  transition: "background 120ms ease",
};
