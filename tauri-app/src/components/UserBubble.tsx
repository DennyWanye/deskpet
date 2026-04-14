import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";

/** CSS fade duration — must stay in sync with the `transition` below. */
const FADE_DURATION_MS = 400;

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
 *
 * 生命周期：
 * 1. 新 text 进来 → snapshot 到 content、opacity 1
 * 2. visibleMs 后 → opacity 0（触发 CSS fade）
 * 3. visibleMs + FADE_DURATION_MS 后 → content null，节点彻底从 DOM 移除
 *    （避免屏幕阅读器读隐藏文字，也让 E2E locator.count() 归零）
 */
export function UserBubble({ text, visibleMs = 2000 }: Props) {
  const [opacity, setOpacity] = useState(0);
  const [content, setContent] = useState<string | null>(null);
  // visibleMs 用 ref 承载：避免把它放进 effect deps 导致新 text 来之前
  // 单独改 visibleMs 会重启计时器（不是 spec 意图）。
  const visibleMsRef = useRef(visibleMs);
  visibleMsRef.current = visibleMs;

  useEffect(() => {
    if (!text) return;
    // App.tsx 追加尾部 U+200B 作为"强制刷新"令牌（保证相同文本重发时
    // 字符串 prop 仍不等，effect 仍会重跑）。渲染时剥掉，避免零宽字符
    // 进入 DOM —— 部分屏幕阅读器会把 ZWSP 念出来。
    setContent(text.replace(/\u200B+$/, ""));
    setOpacity(1);
    const fadeTimer = window.setTimeout(
      () => setOpacity(0),
      visibleMsRef.current,
    );
    const removeTimer = window.setTimeout(
      () => setContent(null),
      visibleMsRef.current + FADE_DURATION_MS,
    );
    return () => {
      window.clearTimeout(fadeTimer);
      window.clearTimeout(removeTimer);
    };
  }, [text]);

  if (!content) return null;

  return (
    <div
      data-testid="user-bubble-fleeting"
      style={{
        ...bubbleStyle,
        opacity,
        transition: `opacity ${FADE_DURATION_MS}ms ease-out`,
        // 气泡只做视觉确认，从不接受点击
        pointerEvents: "none",
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
