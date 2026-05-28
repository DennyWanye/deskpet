// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P4-S23 — bottom input bar for the code panel.
 *
 * - Multi-line textarea with Enter-to-send (Shift+Enter newline).
 * - Sends `chat_v2` with the active session_id stamped in payload.
 * - Concurrency-limited via `chatLimiter` so 5 tiles all sending at
 *   once won't smash the relay with parallel requests.
 */
import { useState, useCallback, useRef, useEffect } from "react";

import { useSessionsStore, chatLimiter } from "../stores/sessionsStore";
import { codePanelWS } from "./ws";

export function InputBar({
  placeholder,
  sessionId,
  leftAccessory,
}: {
  placeholder?: string;
  sessionId?: string;
  /** Optional control rendered at the start of the input row (e.g. the
   *  message panel's mic button). code-panel passes none → unchanged. */
  leftAccessory?: React.ReactNode;
} = {}) {
  const [text, set_text] = useState("");
  const taRef = useRef<HTMLTextAreaElement>(null);

  const active_sid = useSessionsStore((s) => s.active_sid);
  // When `sessionId` is passed (message-panel → "default"), this bar
  // sends/echoes/stops on THAT session — exactly the one the pet's
  // main thread uses. Unset (code-panel) → falls back to active_sid =
  // zero behavior change for code-panel.
  const sid = sessionId ?? active_sid;
  const session = useSessionsStore((s) => s.sessions[sessionId ?? s.active_sid]);
  const inflight_count = useSessionsStore((s) => s.inflight_count);
  const inflight_max = useSessionsStore((s) => s.inflight_max);

  // Auto-grow textarea up to ~120px, then scroll inside.
  useEffect(() => {
    if (!taRef.current) return;
    taRef.current.style.height = "auto";
    taRef.current.style.height = Math.min(taRef.current.scrollHeight, 120) + "px";
  }, [text]);

  const send = useCallback(async () => {
    const t = text.trim();
    if (!t) return;
    if (!sid) return;
    set_text("");
    useSessionsStore.getState().push_message(sid, {
      role: "user",
      text: t,
    });
    useSessionsStore.getState().upsert(sid, {
      status: "thinking",
      inflight: true,
    });
    // P4-S25 B3: inflight is cleared by ws dispatch on chat_v2_final
    // / chat_v2_error / chat_v2_interrupted (NOT here right after the
    // send returns, which used to clear it within ms while the LLM
    // was still computing — that meant the button never visibly
    // toggled). Concurrency-limit the actual send though.
    void chatLimiter.run(async () => {
      codePanelWS.send({
        type: "chat_v2",
        payload: { text: t, session_id: sid },
      });
    });
  }, [text, sid]);

  // P4-S25 B3: stop the in-flight chat for the active session.
  const stop = useCallback(() => {
    if (!sid) return;
    codePanelWS.send({
      type: "chat_v2_interrupt",
      payload: { session_id: sid },
    });
    // Optimistic clear; ws dispatch's chat_v2_interrupted echo will
    // confirm. If the cancel raced and the task already finished
    // emitting final, inflight already cleared — no harm.
    useSessionsStore.getState().upsert(sid, { inflight: false, status: "idle" });
  }, [sid]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // `isComposing` lives on the underlying KeyboardEvent (IME state)
    // — React's wrapper exposes it via nativeEvent.
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      // While inflight, Enter triggers stop instead of send (matches
      // the visible button label).
      if (session?.inflight) {
        stop();
      } else {
        void send();
      }
    }
  };

  const status = session?.status ?? "idle";
  const queued = Math.max(0, inflight_count - inflight_max);
  const inflight = !!session?.inflight;

  return (
    <div
      style={{
        borderTop: "1px solid rgba(148, 163, 184, 0.18)",
        background: "rgba(15, 18, 28, 0.95)",
        padding: "10px 14px",
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
        {leftAccessory}
        <textarea
          ref={taRef}
          value={text}
          onChange={(e) => set_text(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={
            placeholder ??
            (session?.project_root
              ? `跟 LLM 说点什么 — 当前项目: ${session.project_name}`
              : "输入消息开始 Code 模式聊天...")
          }
          rows={1}
          style={{
            flex: 1,
            resize: "none",
            background: "rgba(30, 35, 48, 0.85)",
            color: "#e2e8f0",
            border: "1px solid rgba(148, 163, 184, 0.22)",
            borderRadius: 8,
            padding: "8px 10px",
            fontSize: 13,
            lineHeight: 1.5,
            fontFamily: "inherit",
            minHeight: 36,
            maxHeight: 120,
            outline: "none",
          }}
        />
        <button
          type="button"
          onClick={() => (inflight ? stop() : void send())}
          disabled={!inflight && !text.trim()}
          style={{
            background: inflight
              ? "#dc2626"
              : text.trim()
                ? "#2563eb"
                : "rgba(148, 163, 184, 0.2)",
            color: "#fff",
            border: "none",
            borderRadius: 8,
            padding: "8px 16px",
            fontSize: 13,
            fontWeight: 600,
            cursor: inflight || text.trim() ? "pointer" : "not-allowed",
            height: 36,
          }}
        >
          {inflight ? "■ 停止" : "发送"}
        </button>
      </div>
      <div
        style={{
          display: "flex",
          gap: 12,
          fontSize: 11,
          color: "#94a3b8",
        }}
      >
        <StatusPill status={status} />
        {queued > 0 && (
          <span style={{ color: "#fde68a" }}>
            等待中: {queued}（the relay 并发上限 {inflight_max}）
          </span>
        )}
        <span style={{ marginLeft: "auto", opacity: 0.6 }}>
          Enter 发送 · Shift+Enter 换行
        </span>
      </div>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const map: Record<string, { label: string; color: string }> = {
    idle: { label: "✓ 空闲", color: "#86efac" },
    thinking: { label: "⏳ 思考中", color: "#fde68a" },
    running: { label: "🔧 工具执行中", color: "#67e8f9" },
    permission: { label: "🔒 等待授权", color: "#f59e0b" },
    error: { label: "✗ 错误", color: "#fca5a5" },
  };
  const m = map[status] ?? { label: status, color: "#94a3b8" };
  return <span style={{ color: m.color }}>{m.label}</span>;
}
