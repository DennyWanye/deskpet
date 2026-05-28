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
import { useEffect, useMemo, useRef, type CSSProperties } from "react";

import type { InboxItem } from "../stores/sessionsStore";

export type StreamFilter = "all" | "chat" | "warn" | "err";

export interface ChatStreamMessage {
  role: "user" | "assistant";
  text: string;
  ts: number;
}

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
      role: "user" | "assistant";
      text: string;
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
  user: { bg: "rgba(59, 130, 246, 0.85)", fg: "#f0f6ff" },
  asst: { bg: "rgba(30, 30, 50, 0.85)",   fg: "#e5e7eb" },
} as const;

export function MessageStreamPanel({
  filter,
  chatMessages,
  warnings,
  errors,
  onSetFilter,
  onDismiss,
  onDismissAll,
  onJumpToSession,
  onChoice,
  embedded = false,
}: MessageStreamPanelProps) {
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

  return (
    <div
      role="region"
      aria-label="桌宠消息流"
      data-testid="msgstream-panel"
      style={embedded ? embeddedWrapperStyle : wrapperStyle}
    >
      <header style={headerStyle}>
        <FilterChip
          label="全部"
          active={filter === "all"}
          onClick={() => onSetFilter("all")}
          testId="msgstream-filter-all"
        />
        <FilterChip
          label="对话"
          active={filter === "chat"}
          onClick={() => onSetFilter("chat")}
          testId="msgstream-filter-chat"
        />
        <FilterChip
          label={`⚠ ${warnings.length || ""}`.trim()}
          active={filter === "warn"}
          tone="warn"
          onClick={() => onSetFilter("warn")}
          testId="msgstream-filter-warn"
        />
        <FilterChip
          label={`🚨 ${errors.length || ""}`.trim()}
          active={filter === "err"}
          tone="err"
          onClick={() => onSetFilter("err")}
          testId="msgstream-filter-err"
        />
      </header>

      {/* Sweep buttons only show in alert-only filters. */}
      {(filter === "warn" || filter === "err") &&
        (filter === "warn" ? warnings.length > 1 : errors.length > 1) && (
          <div style={sweepBarStyle}>
            <button
              type="button"
              onClick={() =>
                onDismissAll(filter === "warn" ? "yellow" : "red")
              }
              data-testid="msgstream-dismiss-all"
              style={pillButton(
                filter === "warn" ? PALETTE.warn.accent : PALETTE.err.accent,
                filter === "warn" ? PALETTE.warn.border : PALETTE.err.border,
              )}
            >
              全部已读
            </button>
          </div>
        )}

      <div ref={listRef} style={listStyle}>
        {rows.length === 0 ? (
          <div style={emptyStyle}>
            {emptyMessage(filter)}
          </div>
        ) : (
          rows.map((r) =>
            r.kind === "chat" ? (
              <ChatRow key={r.key} role={r.role} text={r.text} ts={r.ts} />
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
        role: m.role,
        text: m.text,
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

function ChatRow({
  role,
  text,
  ts,
}: {
  role: "user" | "assistant";
  text: string;
  ts: number;
}) {
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

function FilterChip({
  label,
  active,
  tone,
  onClick,
  testId,
}: {
  label: string;
  active: boolean;
  tone?: "warn" | "err";
  onClick: () => void;
  testId?: string;
}) {
  const accent = tone ? PALETTE[tone].accent : "#67e8f9";
  const border = tone ? PALETTE[tone].border : "rgba(103, 232, 249, 0.40)";
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      style={{
        fontSize: 10.5,
        fontWeight: active ? 700 : 500,
        padding: "3px 8px",
        marginRight: 4,
        borderRadius: 5,
        border: `1px solid ${active ? border : "rgba(148, 163, 184, 0.20)"}`,
        background: active ? (tone ? PALETTE[tone].soft : "rgba(103, 232, 249, 0.18)") : "transparent",
        color: active ? accent : "#cbd5e1",
        cursor: "pointer",
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </button>
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

const headerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  flexWrap: "wrap",
  rowGap: 4,
  padding: "6px 6px 6px 8px",
  borderBottom: "1px solid rgba(148, 163, 184, 0.18)",
};

const sweepBarStyle: CSSProperties = {
  display: "flex",
  justifyContent: "flex-end",
  padding: "4px 8px",
  borderBottom: "1px dashed rgba(148, 163, 184, 0.15)",
};

const listStyle: CSSProperties = {
  flex: 1,
  overflowY: "auto",
  padding: 6,
  display: "flex",
  flexDirection: "column",
  gap: 5,
};

const rowBaseStyle: CSSProperties = {
  maxWidth: "92%",
  padding: "6px 9px",
  borderRadius: 8,
  border: "1px solid transparent",
  display: "flex",
  flexDirection: "column",
  gap: 3,
  wordBreak: "break-word",
  whiteSpace: "pre-wrap",
};

const metaStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  fontSize: 9.5,
  color: "#9ca3af",
};

const bodyStyle: CSSProperties = {
  fontSize: 11.5,
  lineHeight: 1.45,
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
