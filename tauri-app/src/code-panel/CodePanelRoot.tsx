/**
 * P4-S23 — root component for the secondary "code-panel" Tauri window.
 *
 * Layout:
 *   ┌── Header (project / status / close) ──────────────────────────┐
 *   ├── SessionSidebar ──┬── Main view (chat / dashboard) ──────────┤
 *   │                    │  - chat: MessageStream + InputBar         │
 *   │                    │  - dashboard: SessionGridView             │
 *   └────────────────────┴─────────────────────────────────────────┘
 *
 * The WS dispatcher (`./ws.ts`) auto-connects on import; we just
 * subscribe to the zustand store from here.
 */
import { useState, useMemo, useCallback } from "react";
import { invoke } from "@tauri-apps/api/core";

import { useSessionsStore, collect_inbox } from "../stores/sessionsStore";
import {
  MessageStreamPanel,
  type StreamFilter,
} from "../components/MessageStreamPanel";
import { forPet } from "../petText";
import { SessionSidebar } from "./SessionSidebar";
import { MessageStream } from "./MessageStream";
import { InputBar } from "./InputBar";
import { SessionGridView } from "./SessionGridView";
import { codePanelWS } from "./ws"; // also auto-connects WS as side-effect

type View = "chat" | "dashboard";

export function CodePanelRoot() {
  // P4-S23 UX fix: default to dashboard view when no real code
  // session exists. The panel now opens as soon as the user clicks
  // 🔧; project selection happens INSIDE the panel via "+ 新项目"
  // (no forced upfront folder picker).
  const sessions = useSessionsStore((s) => s.sessions);
  const has_real_session = Object.values(sessions).some(
    (s) => s.code_session_id || s.base_session_id !== "default",
  );
  const [view, set_view] = useState<View>(
    has_real_session ? "chat" : "dashboard",
  );

  const session = useSessionsStore((s) => s.sessions[s.active_sid]);
  const messages = session?.messages ?? [];

  // 2026-05-16: 左侧「信息显示区域」—— 复用桌宠窗 MessageStreamPanel
  // (embedded) 显示本 session 聊天流 + supervisor ⚠/🚨 inbox。数据全在
  // 共享 zustand store；chatMessages 用 App.tsx 同款 forPet 清洗（滤
  // <think>/工具 trace），warnings/errors 用 collect_inbox。
  const [streamCollapsed, setStreamCollapsed] = useState(false);
  const [streamFilter, setStreamFilter] = useState<StreamFilter>("all");
  const set_active = useSessionsStore((s) => s.set_active);
  const dismiss_alert = useSessionsStore((s) => s.dismiss_alert);
  const dismiss_all_alerts = useSessionsStore((s) => s.dismiss_all_alerts);
  const clear_supervisor_alert = useSessionsStore(
    (s) => s.clear_supervisor_alert,
  );

  const streamChat = useMemo(
    () =>
      messages.flatMap((m, i) => {
        const ts = Date.now() - (messages.length - i) * 1000;
        if (m.role === "user") {
          return [{ role: "user" as const, text: m.text ?? "", ts }];
        }
        // assistant: 滤掉工具调用/结果/错误 trace + 仅 think 的块，
        // 剥 <think>。这些噪声不进信息区（完整内容仍在右侧主流/历史）。
        const clean = forPet(m.text);
        if (!clean) return [];
        return [{ role: "assistant" as const, text: clean, ts }];
      }),
    [messages],
  );
  const warningItems = useMemo(
    () => collect_inbox(sessions, "yellow"),
    [sessions],
  );
  const errorItems = useMemo(
    () => collect_inbox(sessions, "red"),
    [sessions],
  );

  const jumpToSession = useCallback(
    (sid: string) => {
      set_active(sid);
      set_view("chat");
    },
    [set_active],
  );
  const handleAlertChoice = useCallback(
    (
      sid: string,
      alert_id: string,
      button_index: number,
      button_text: string,
    ) => {
      try {
        codePanelWS.send({
          type: "supervisor_user_choice",
          payload: {
            session_id: sid,
            alert_id,
            button_index,
            button_text,
          },
        });
      } catch (e) {
        console.warn("[code-panel] supervisor_user_choice send failed:", e);
      }
      dismiss_alert(sid, alert_id);
      const cur = sessions[sid]?.supervisor_alert;
      if (cur && cur.alert_id === alert_id) {
        clear_supervisor_alert(sid);
      }
    },
    [dismiss_alert, clear_supervisor_alert, sessions],
  );

  const close_panel = async () => {
    // P4-S24 followup: clicking the inline ✕ should be equivalent to
    // clicking the pet's "退出" banner button — both must:
    //   1. tell the backend to exit code mode (so codeModeState flips
    //      to enabled=false, the pet's green Code 模式 banner clears,
    //      and the toolbar 🔧 highlight goes away);
    //   2. THEN hide the panel window.
    // Order matters: send the exit IPC FIRST so the backend's
    // code_mode_state broadcast arrives before the panel webview goes
    // away. Otherwise pet-side state stays stuck on "still in code
    // mode" until next chat or backend reconnect.
    try {
      codePanelWS.send({ type: "code_mode_exit" });
    } catch (e) {
      console.warn("[code-panel] code_mode_exit send failed:", e);
    }
    try {
      await invoke("close_code_panel");
    } catch (e) {
      console.warn("[code-panel] close failed:", e);
    }
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        width: "100vw",
        color: "#e2e8f0",
        fontFamily:
          '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
      }}
    >
      {/* Top bar — P4-S25: removed the "← chat" button. Sidebar
          project click now switches view to chat directly (see
          SessionSidebar's onPick prop), so the back-and-forth nav
          becomes a single click instead of click-then-click. The
          "⊞ 仪表盘" button stays as the only explicit way to surface
          the multi-project dashboard from inside chat. */}
      <header
        style={{
          height: 40,
          padding: "0 14px",
          display: "flex",
          alignItems: "center",
          gap: 12,
          background: "rgba(15, 18, 28, 0.98)",
          borderBottom: "1px solid rgba(148, 163, 184, 0.18)",
          fontSize: 12.5,
        }}
      >
        <strong style={{ fontSize: 13 }}>🔧 DeskPet · Code Mode</strong>
        <span style={{ color: "#94a3b8" }}>·</span>
        <span
          data-bp-selectable=""
          style={{
            color: "#cbd5e1",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            flex: 1,
          }}
          title={session?.project_root ?? ""}
        >
          {view === "dashboard"
            ? "多项目仪表盘"
            : session?.project_root ?? "(选择一个项目)"}
        </span>
        {/* Only the "show dashboard" button remains. The previous
            "← chat" return button is gone — sidebar click handles
            that now (see SessionSidebar.onPick). */}
        {view === "chat" && (
          <button
            type="button"
            onClick={() => set_view("dashboard")}
            title="多项目仪表盘"
            style={btnStyle}
          >
            ⊞ 仪表盘
          </button>
        )}
        <button type="button" onClick={close_panel} title="关闭面板" style={btnStyle}>
          ✕
        </button>
      </header>

      {/* Body — 3 列：侧栏 | 信息显示区域 | 聊天主区 (2026-05-16) */}
      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
        <SessionSidebar
          onShowDashboard={() => set_view("dashboard")}
          onPick={() => set_view("chat")}
        />
        {view === "chat" ? (
          <>
            {/* 信息显示区域 —— 聊天流 + supervisor 告警，复用桌宠窗
                MessageStreamPanel(embedded)。折叠时收成窄条。 */}
            <div
              style={{
                width: streamCollapsed ? 18 : 320,
                flexShrink: 0,
                height: "100%",
                display: "flex",
                overflow: "hidden",
              }}
            >
              <MessageStreamPanel
                embedded
                collapsed={streamCollapsed}
                filter={streamFilter}
                chatMessages={streamChat}
                warnings={warningItems}
                errors={errorItems}
                onCollapse={() => setStreamCollapsed(true)}
                onExpand={() => setStreamCollapsed(false)}
                onSetFilter={setStreamFilter}
                onDismiss={dismiss_alert}
                onDismissAll={dismiss_all_alerts}
                onJumpToSession={jumpToSession}
                onChoice={handleAlertChoice}
              />
            </div>
            <div
              style={{
                flex: 1,
                display: "flex",
                flexDirection: "column",
                background: "#0f1218",
                overflow: "hidden",
              }}
            >
              <div style={{ flex: 1, overflow: "hidden" }}>
                <MessageStream messages={messages} />
              </div>
              <InputBar />
            </div>
          </>
        ) : (
          <SessionGridView onSelectSession={() => set_view("chat")} />
        )}
      </div>
    </div>
  );
}

const btnStyle: React.CSSProperties = {
  background: "rgba(148, 163, 184, 0.16)",
  color: "#e2e8f0",
  border: "1px solid rgba(148, 163, 184, 0.22)",
  borderRadius: 5,
  fontSize: 11.5,
  padding: "4px 10px",
  cursor: "pointer",
};
