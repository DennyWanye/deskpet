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
  bottom: "44px",
  left: "5px",
  right: "5px",
  height: "60px",
  backgroundColor: "rgba(20, 20, 35, 0.92)",
  borderRadius: "10px",
  border: "1px solid rgba(129,140,248,0.35)",
  padding: "8px 34px 8px 12px",
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
