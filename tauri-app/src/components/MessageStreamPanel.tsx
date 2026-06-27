// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P5-S3-Inbox v2 — Left-side message stream.
 *
 * Replaces the modal `ChatHistoryPanel` + the floating `PetSupervisorBubble`
 * with a single persistent panel that lives on the left side of the
 * pet window. It carries every message the user might want to see:
 *
 *   • companion chat (user ↔ assistant)
 *   • supervisor warnings  (yellow severity)
 *   • supervisor errors    (red severity)
 *
 * A row of filter chips at the top lets the user narrow the stream:
 * "全部 / 对话 / 提醒(N) / 错误(N)". The toolbar buttons (💬 / ⚠ / 🚨)
 * call `onSetFilter` to switch tabs without forcing the user to click
 * inside the panel.
 *
 * Visibility model:
 *   • The panel is always *mounted* — chat history doesn't disappear
 *     just because the user collapsed it.
 *   • Pressing ✕ collapses to a thin handle on the left edge; clicking
 *     the handle (or any toolbar button) re-expands.
 *   • A fresh red error auto-expands the panel + switches to "错误".
 */
import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  type CSSProperties,
} from "react";

// 消息流滚动位置持久键(进入消息界面恢复上次位置,见下 useLayoutEffect)。
const MSGSTREAM_SCROLL_KEY = "deskpet.msgstream.scroll.v1";

import type { InboxItem, Message } from "../stores/sessionsStore";
// 子代理并发进度卡片（深色变体，与本面板玻璃拟态一致）。runs 空时自渲染 null，
// 零侵入；数据由本窗口 codePanelWS 的 subagent_progress 派发喂 subagentStore。
import { SubagentProgressPanel } from "../code-panel/SubagentProgressPanel";
import { PPTOutlineCard } from "../code-panel/PPTOutlineCard";

export type StreamFilter = "all" | "chat" | "warn" | "err";

export type ChatStreamMessage =
  | {
      // 2026-06-12: 加 "tool" — 工具执行轨迹(调用/结果)进主消息流,
      // 用户全程可观测(此前派生层把 tool_call/tool_result 滤掉了)。
      role: "user" | "assistant" | "tool";
      text: string;
      ts: number;
    }
  | {
      role: "ppt_outline";
      message: Message;
      session_id: string;
      ts: number;
    };

export interface MessageStreamPanelProps {
  filter: StreamFilter;
  /** Companion-mode chat messages, oldest → newest. Internally we sort
   * by ts when merging with alerts. */
  chatMessages: ChatStreamMessage[];
  warnings: InboxItem[];
  errors: InboxItem[];
  onSetFilter: (f: StreamFilter) => void;
  onDismiss: (sid: string, alert_id: string) => void;
  onDismissAll: (severity: "yellow" | "red") => void;
  onJumpToSession: (sid: string) => void;
  onChoice: (
    sid: string,
    alert_id: string,
    button_index: number,
    button_text: string,
  ) => void;
  /** 2026-05-16: pet window = floating absolute overlay (default,
   * unchanged). Code-mode window embeds this in a 3-column flex layout
   * → embedded=true switches the wrapper from absolute to a relative
   * flex-fill panel (no top/left/width hardcode). Visual styling
   * identical; only positioning differs. Default false keeps the pet
   * window byte-identical (zero regression). */
  embedded?: boolean;
}

type StreamRow =
  | {
      kind: "chat";
      ts: number;
      msg: ChatStreamMessage;
      key: string;
    }
  | {
      kind: "alert";
      ts: number;
      severity: "yellow" | "red";
      item: InboxItem;
      key: string;
    };

const PALETTE = {
  warn: { accent: "#f59e0b", soft: "rgba(245, 158, 11, 0.18)", border: "rgba(245, 158, 11, 0.45)" },
  err:  { accent: "#ef4444", soft: "rgba(239, 68, 68, 0.18)", border: "rgba(239, 68, 68, 0.45)" },
  user: { bg: "rgba(59, 130, 246, 0.92)", fg: "#f5f9ff" },
  asst: { bg: "rgba(255, 255, 255, 0.055)", fg: "#e8edf6" },
} as const;

export function MessageStreamPanel({
  filter,
  chatMessages,
  warnings,
  errors,
  onDismiss,
  onJumpToSession,
  onChoice,
  embedded = false,
}: MessageStreamPanelProps) {
  // 注：onSetFilter / onDismissAll 仍保留在 MessageStreamPanelProps 类型里
  // （调用方照常传），但当前 render 未用到——半接线的过滤条特性。先不解构
  // 以通过 tsc noUnusedParameters；要恢复过滤条 UI 时再接回。
  const rows = useMemo(
    () => buildRows(chatMessages, warnings, errors, filter),
    [chatMessages, warnings, errors, filter],
  );

  // Auto-scroll to newest row when it changes (only if user hasn't
  // scrolled up — the simple heuristic is "we're already near the
  // bottom"). Resilient to React batching; we read scrollTop just
  // after layout.
  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    const nearBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (nearBottom) {
      el.scrollTop = el.scrollHeight;
    }
  }, [rows.length]);

  // 进入消息界面时恢复上次滚动位置(关掉消息面板再打开仍回到原处)。
  // 消息大框是独立 webview,开关可能重建 → 用 localStorage 持久,跨 webview 重建有效。
  // 上次在底部 → 仍贴底(随新消息跟随);否则恢复到当时的 scrollTop。仅挂载时跑一次。
  const scrollRestoredRef = useRef(false);
  useLayoutEffect(() => {
    const el = listRef.current;
    if (!el || scrollRestoredRef.current) return;
    scrollRestoredRef.current = true;
    let saved: { top: number; atBottom: boolean } | null = null;
    try {
      saved = JSON.parse(localStorage.getItem(MSGSTREAM_SCROLL_KEY) || "null");
    } catch {
      saved = null;
    }
    if (!saved || saved.atBottom) {
      el.scrollTop = el.scrollHeight; // 默认/上次贴底 → 底部
    } else {
      el.scrollTop = Math.max(0, Math.min(saved.top, el.scrollHeight));
    }
  }, []);

  const handleListScroll = () => {
    const el = listRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    try {
      localStorage.setItem(
        MSGSTREAM_SCROLL_KEY,
        JSON.stringify({ top: el.scrollTop, atBottom }),
      );
    } catch {
      /* localStorage 不可用时忽略,不影响功能 */
    }
  };

  return (
    <div
      role="region"
      aria-label="桌宠消息流"
      data-testid="msgstream-panel"
      style={embedded ? embeddedWrapperStyle : wrapperStyle}
    >
      {/* 2026-05-31 restore — 用户要求删掉 4 个 filter tab（全部/对话/⚠/🚨）。
          filter prop 保留为 "all"（caller 默认值），所有内容混排。sweep bar
          (filter==warn|err 才显示) 在 filter 锁定 "all" 后自然不再渲染。
          对应 onSetFilter / onDismissAll / FilterChip / sweepBarStyle / pillButton
          变成未使用，但 prop 接口保留以兼容 caller。 */}

      {/* 子代理并发实时进度：钉在消息流顶部（list 之上，不随滚动），始终可见。
          无并发任务时该组件返回 null，不占位。 */}
      <SubagentProgressPanel variant="dark" />

      <div ref={listRef} style={listStyle} onScroll={handleListScroll}>
        {rows.length === 0 ? (
          <div style={emptyStyle}>
            {emptyMessage(filter)}
          </div>
        ) : (
          rows.map((r) =>
            r.kind === "chat" ? (
              <ChatRow key={r.key} msg={r.msg} />
            ) : (
              <AlertRow
                key={r.key}
                item={r.item}
                severity={r.severity}
                onDismiss={() => onDismiss(r.item.session_id, r.item.alert_id)}
                onJump={() => onJumpToSession(r.item.session_id)}
                onChoice={(idx, text) =>
                  onChoice(r.item.session_id, r.item.alert_id, idx, text)
                }
              />
            ),
          )
        )}
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// Row builders + sub-components
// ----------------------------------------------------------------------

function buildRows(
  chats: ChatStreamMessage[],
  warns: InboxItem[],
  errs: InboxItem[],
  filter: StreamFilter,
): StreamRow[] {
  const rows: StreamRow[] = [];
  if (filter === "all" || filter === "chat") {
    chats.forEach((m, i) =>
      rows.push({
        kind: "chat",
        ts: m.ts,
        msg: m,
        key: `c:${i}:${m.ts}`,
      }),
    );
  }
  if (filter === "all" || filter === "warn") {
    warns.forEach((a) =>
      rows.push({
        kind: "alert",
        ts: a.received_at,
        severity: "yellow",
        item: a,
        key: `y:${a.session_id}:${a.alert_id}`,
      }),
    );
  }
  if (filter === "all" || filter === "err") {
    errs.forEach((a) =>
      rows.push({
        kind: "alert",
        ts: a.received_at,
        severity: "red",
        item: a,
        key: `r:${a.session_id}:${a.alert_id}`,
      }),
    );
  }
  // Oldest → newest so newest sits at the bottom (chat-stream UX).
  rows.sort((a, b) => a.ts - b.ts);
  return rows;
}

function emptyMessage(f: StreamFilter): string {
  switch (f) {
    case "chat": return "本次还没聊过。";
    case "warn": return "暂无未处理的提醒。";
    case "err":  return "暂无未处理的错误。";
    default:     return "（消息流为空）";
  }
}

function ChatRow({ msg }: { msg: ChatStreamMessage }) {
  if (msg.role === "ppt_outline") {
    const m = msg.message;
    return (
      <div style={{ width: "100%" }}>
        <PPTOutlineCard
          outlineId={m.outline_id ?? ""}
          topic={m.topic ?? ""}
          outlineMd={m.outline_md ?? ""}
          sourcesCount={m.sources_count ?? 0}
          noResearch={!!m.no_research}
          history={m.history ?? []}
          awaiting={!!m.ppt_outline_awaiting}
          sessionId={msg.session_id}
        />
      </div>
    );
  }

  const { role, text, ts } = msg;
  if (role === "tool") {
    // 工具执行轨迹行: 紧凑、低调(灰底等宽小字),不抢聊天主体视觉。
    return (
      <div
        style={{
          ...rowBaseStyle,
          alignSelf: "flex-start",
          background: "rgba(20, 28, 40, 0.7)",
          color: "#94a3b8",
          borderColor: "rgba(103, 232, 249, 0.18)",
          padding: "4px 10px",
          fontSize: 11.5,
          fontFamily: "Consolas, 'Courier New', monospace",
          maxWidth: "92%",
        }}
        data-role="tool"
      >
        <div data-bp-selectable="" style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
          {text || "(工具)"}
          <span style={{ marginLeft: 8, opacity: 0.5 }}>{format_relative(ts)}</span>
        </div>
      </div>
    );
  }
  const tone = role === "user" ? PALETTE.user : PALETTE.asst;
  return (
    <div
      style={{
        ...rowBaseStyle,
        alignSelf: role === "user" ? "flex-end" : "flex-start",
        background: tone.bg,
        color: tone.fg,
        borderColor: "rgba(255,255,255,0.06)",
      }}
      data-role={role}
    >
      <div style={metaStyle}>
        <span>{role === "user" ? "我" : "桌宠"}</span>
        <span style={{ marginLeft: "auto", opacity: 0.6 }}>
          {format_relative(ts)}
        </span>
      </div>
      {/* data-bp-selectable: 让消息正文可被鼠标拖选复制（index.css 的
          全局 user-select:none 默认会挡住）。 */}
      <div data-bp-selectable="" style={bodyStyle}>{text || "(空)"}</div>
    </div>
  );
}

function AlertRow({
  item,
  severity,
  onDismiss,
  onJump,
  onChoice,
}: {
  item: InboxItem;
  severity: "yellow" | "red";
  onDismiss: () => void;
  onJump: () => void;
  onChoice: (idx: number, text: string) => void;
}) {
  const tone = severity === "yellow" ? PALETTE.warn : PALETTE.err;
  const sid_short =
    item.project_name && item.project_name !== item.session_id
      ? item.project_name
      : item.session_id.length > 18
      ? `${item.session_id.slice(0, 16)}…`
      : item.session_id;
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onJump}
      style={{
        ...rowBaseStyle,
        alignSelf: "stretch",
        background: "rgba(255,255,255,0.03)",
        border: `1px solid ${tone.border}`,
        cursor: "pointer",
      }}
    >
      <div style={metaStyle}>
        <span
          style={{
            padding: "1px 5px",
            background: tone.soft,
            color: tone.accent,
            borderRadius: 4,
            fontWeight: 600,
            marginRight: 6,
          }}
        >
          {severity === "yellow" ? "⚠" : "🚨"} {sid_short}
        </span>
        <span style={{ marginLeft: "auto", opacity: 0.6 }}>
          {format_relative(item.received_at)}
        </span>
      </div>
      {/* data-bp-selectable: 告警正文可被拖选复制。拖选时浏览器不触发
          click，行级 onClick 跳转不受影响。 */}
      <div data-bp-selectable="" style={bodyStyle}>
        {item.user_message || item.diagnosis || "(supervisor 未提供详情)"}
      </div>
      <div
        style={actionRowStyle}
        onClick={(e) => e.stopPropagation()}
      >
        {item.suggested_buttons.slice(0, 2).map((b, i) => (
          <button
            key={i}
            type="button"
            data-testid={`msgstream-choice-${i}`}
            onClick={(e) => {
              e.stopPropagation();
              onChoice(i, b);
            }}
            style={{
              ...pillButton(tone.accent, tone.border),
              padding: "3px 9px",
              fontWeight: 600,
            }}
          >
            {b}
          </button>
        ))}
        <button
          type="button"
          data-testid="msgstream-dismiss"
          onClick={(e) => {
            e.stopPropagation();
            onDismiss();
          }}
          style={pillButton("#9ca3af", "rgba(148, 163, 184, 0.30)")}
          title="标记已处理"
        >
          已知道
        </button>
      </div>
    </div>
  );
}

function pillButton(color: string, border: string): CSSProperties {
  return {
    fontSize: 10.5,
    fontWeight: 500,
    padding: "2px 8px",
    borderRadius: 5,
    border: `1px solid ${border}`,
    background: "transparent",
    color,
    cursor: "pointer",
    whiteSpace: "nowrap",
  };
}

function format_relative(ts: number, now: number = Date.now()): string {
  const delta_s = Math.max(0, Math.round((now - ts) / 1000));
  if (delta_s < 60) return `${delta_s}s 前`;
  if (delta_s < 3600) return `${Math.round(delta_s / 60)}m 前`;
  if (delta_s < 86400) return `${Math.round(delta_s / 3600)}h 前`;
  return `${Math.round(delta_s / 86400)}d 前`;
}

// ----------------------------------------------------------------------
// Style constants
// ----------------------------------------------------------------------

const wrapperStyle: CSSProperties = {
  position: "absolute",
  top: 56,
  left: 8,
  width: 248,
  maxHeight: "calc(100vh - 110px)",
  zIndex: 22,
  pointerEvents: "auto",
  background: "rgba(15, 18, 28, 0.92)",
  border: "1px solid rgba(148, 163, 184, 0.25)",
  borderRadius: 10,
  boxShadow: "0 8px 24px rgba(0,0,0,0.5)",
  backdropFilter: "blur(12px)",
  color: "#e5e7eb",
  fontSize: 11.5,
  display: "flex",
  flexDirection: "column",
  overflow: "hidden",
};

// embedded variant — 填满父 flex 格（父格给定宽高）。
const embeddedWrapperStyle: CSSProperties = {
  position: "relative",
  height: "100%",
  width: "100%",
  background: "rgba(15, 18, 28, 0.92)",
  borderRight: "1px solid rgba(148, 163, 184, 0.18)",
  color: "#e5e7eb",
  fontSize: 11.5,
  display: "flex",
  flexDirection: "column",
  overflow: "hidden",
};

const listStyle: CSSProperties = {
  flex: 1,
  overflowY: "auto",
  padding: "14px 14px 8px",
  display: "flex",
  flexDirection: "column",
  gap: 12,
};

const rowBaseStyle: CSSProperties = {
  maxWidth: "84%",
  padding: "9px 13px",
  borderRadius: 14,
  border: "1px solid transparent",
  display: "flex",
  flexDirection: "column",
  gap: 4,
  wordBreak: "break-word",
  whiteSpace: "pre-wrap",
};

const metaStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  fontSize: 10,
  opacity: 0.65,
  color: "#9ca3af",
};

const bodyStyle: CSSProperties = {
  fontSize: 12.5,
  lineHeight: 1.6,
  color: "inherit",
};

const actionRowStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 4,
  alignItems: "center",
  justifyContent: "flex-end",
  marginTop: 2,
};

const emptyStyle: CSSProperties = {
  textAlign: "center",
  color: "#9ca3af",
  fontSize: 11.5,
  padding: "16px 8px",
  lineHeight: 1.6,
};
