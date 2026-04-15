import type { CSSProperties } from "react";

type Props = {
  /** 最新一条助手消息文本；空则显示 placeholder 引导用户说话 */
  latestAssistant: string | null;
  /** 点击展开历史 */
  onOpenHistory: () => void;
};

// 留白时的引导文案。空字符串版本在盲测里被误以为"坏了"，所以 P2-0-S5
// 起改成软提示；长度压在两行内，避免底栏在 TTS 第一个 token 到达前就
// 先抖一下。保持 muted 色（opacity<1）以跟真实助手回复区分。
const EMPTY_PLACEHOLDER = "按住下方按钮说话，或输入消息开始聊天…";

/**
 * VN 风格底栏对话框。
 *
 * 设计原则：
 * - 固定高度 60px，不随内容弹跳（避免挡 Live2D）
 * - 单条渲染 —— 旧消息直接被新消息替换，无动画（TTS 串流期间闪烁会晕）
 * - 文本超出时在内部 textStyle 区域 scroll，外框高度不变
 */
export function DialogBar({ latestAssistant, onOpenHistory }: Props) {
  const isEmpty = latestAssistant === null || latestAssistant === "";
  return (
    <div
      style={barStyle}
      data-testid="dialog-bar"
    >
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

// Muted + italic 让空态提示从真实助手回复里视觉区分出来，
// 同时 data-empty="true" 给 E2E 一个稳定的断言钩子。
const placeholderStyle: CSSProperties = {
  opacity: 0.5,
  fontStyle: "italic",
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
