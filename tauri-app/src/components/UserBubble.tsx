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
  bottom: "112px",
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
