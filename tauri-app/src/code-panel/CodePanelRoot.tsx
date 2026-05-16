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
import { useState } from "react";
import { invoke } from "@tauri-apps/api/core";

import { useSessionsStore } from "../stores/sessionsStore";
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

      {/* Body — sidebar + main */}
      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
        <SessionSidebar
          onShowDashboard={() => set_view("dashboard")}
          onPick={() => set_view("chat")}
        />
        {view === "chat" ? (
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
