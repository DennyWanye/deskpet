// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * VN-style dialog bar — P4-S20-UI revamp.
 *
 * 设计：
 * - 玻璃质地（深色半透明 + blur）
 * - 助手回复用 13.5px / 1.55 line-height，柔和留白
 * - 空态文案右下用极弱的提示色（不和真实回复争夺视觉注意）
 * - 历史按钮悬浮态高亮
 */
import { useRef, type CSSProperties, type WheelEvent } from "react";

type Props = {
  /** 最新一条助手消息文本；空则显示 placeholder 引导用户说话 */
  latestAssistant: string | null;
};

const EMPTY_PLACEHOLDER = "按住下方按钮说话，或输入消息开始聊天…";

export function DialogBar({ latestAssistant }: Props) {
  const isEmpty = latestAssistant === null || latestAssistant === "";
  const textRef = useRef<HTMLDivElement>(null);

  // 2026-05-16: 固定高度（96px）下长回复需在框内滚动。Tauri 透明
  // always-on-top + 根节点 data-tauri-drag-region 的桌宠窗口里，wheel
  // 事件对非交互子元素传递不可靠，原生 overflowY:auto 滚不动。这里
  // 显式接管 wheel：手动改 scrollTop + stopPropagation（别让 drag-region
  // / 父节点吞掉），保证固定小框内可滚动看全内容。完整内容仍可点 💬
  // 在信息回顾里看。
  const handleWheel = (e: WheelEvent<HTMLDivElement>) => {
    const el = textRef.current;
    if (!el) return;
    if (el.scrollHeight <= el.clientHeight) return; // 无溢出不拦截
    el.scrollTop += e.deltaY;
    e.stopPropagation();
  };

  return (
    <div style={barStyle} data-testid="dialog-bar" className="bp-dialog-bar">
      <div
        ref={textRef}
        data-testid="dialog-bar-assistant"
        data-empty={isEmpty ? "true" : "false"}
        data-bp-selectable=""
        onWheel={handleWheel}
        // 桌宠窗根节点带 `data-tauri-drag-region`：mousedown 冒泡到根
        // 会被 Tauri 当成「拖动窗口」，导致框里文字无法用鼠标拖选复制。
        // 在可选文本容器上 stopPropagation，阻止事件到达 drag-region，
        // 浏览器即可正常进行文本选区/复制（CSS 已 user-select:text）。
        onMouseDown={(e) => e.stopPropagation()}
        style={isEmpty ? { ...textStyle, ...placeholderStyle } : textStyle}
      >
        {isEmpty ? EMPTY_PLACEHOLDER : latestAssistant}
      </div>
    </div>
  );
}

const barStyle: CSSProperties = {
  position: "absolute",
  bottom: 56,
  left: 8,
  right: 8,
  minHeight: 64,
  // 固定高度（VN 紧凑风格，保持原设计）。2026-05-16 修的是「无法滚动」
  // 而非「框太小」：旧 alignItems:center 让 flex 文本子元素拿不到完整
  // 高度 → 内层 overflowY:auto 永不触发，长回复被截断且滚不动。修复在
  // 下方 alignItems:stretch + textStyle.minHeight:0；高度仍保持 96。
  maxHeight: 96,
  // 高级感玻璃：深色渐变 + 高饱和模糊 + 顶部内高光线 + 柔和投影
  background:
    "linear-gradient(180deg, rgba(30,35,52,0.86) 0%, rgba(17,20,30,0.90) 100%)",
  borderRadius: 16,
  border: "1px solid rgba(255,255,255,0.10)",
  padding: "11px 15px",
  color: "#e8edf6",
  fontSize: 13.5,
  lineHeight: 1.6,
  zIndex: 10,
  overflow: "hidden",
  display: "flex",
  // stretch（非 center）：让文本子元素撑满 bar 高度，其 overflowY:auto
  // 才能在 maxHeight 内真正滚动。短消息顶部对齐，可读性更好。
  alignItems: "stretch",
  backdropFilter: "blur(20px) saturate(1.5)",
  WebkitBackdropFilter: "blur(20px) saturate(1.5)",
  boxShadow:
    "0 12px 36px rgba(0,0,0,0.42), inset 0 1px 0 rgba(255,255,255,0.08)",
};

const textStyle: CSSProperties = {
  flex: 1,
  overflowY: "auto",
  minHeight: 0,
  // 2026-05-16 关键修复：用**具体** maxHeight 让这个内层 div 自己成为
  // 明确的滚动容器。之前 maxHeight:"100%" + flex stretch 下内层被撑成
  // 完整内容高度、由外层 barStyle(overflow:hidden) 裁切 → 内层
  // scrollHeight==clientHeight 永不滚动（onWheel guard 也因此 return）。
  // barStyle maxHeight 96 - 上下 padding 20 ≈ 76 可用，取 72 留余量；
  // 短消息时内层按内容收缩（maxHeight 非固定 height），长消息滚动。
  maxHeight: 72,
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  letterSpacing: 0.1,
};

const placeholderStyle: CSSProperties = {
  opacity: 0.55,
  fontStyle: "italic",
  color: "#94a3b8",
};
