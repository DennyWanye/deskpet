// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

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

import { Icon } from "../components/Icon";
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
          height: 44,
          padding: "0 14px",
          display: "flex",
          alignItems: "center",
          gap: 11,
          background:
            "linear-gradient(180deg, rgba(26,30,44,0.98), rgba(16,19,28,0.99))",
          borderBottom: "1px solid rgba(255,255,255,0.07)",
          boxShadow: "inset 0 1px 0 rgba(255,255,255,0.05)",
          fontSize: 12.5,
        }}
      >
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: 26,
            height: 26,
            borderRadius: 8,
            background: "rgba(52,211,153,0.16)",
            color: "#6ee7b7",
          }}
        >
          <Icon name="terminal" size={15} />
        </span>
        <strong style={{ fontSize: 13, fontWeight: 600, letterSpacing: 0.2 }}>
          DeskPet · Code Mode
        </strong>
        <span
          aria-hidden
          style={{
            width: 1,
            height: 16,
            background: "rgba(148,163,184,0.28)",
          }}
        />
        <span
          data-bp-selectable=""
          style={{
            color: "#9aa6b8",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            flex: 1,
            fontFamily:
              '"JetBrains Mono","Cascadia Code",Consolas,monospace',
            fontSize: 11.5,
          }}
          title={session?.project_root ?? ""}
        >
          {view === "dashboard"
            ? "多项目仪表盘"
            : session?.project_root ?? "(选择一个项目)"}
        </span>
        {view === "chat" && (
          <button
            type="button"
            onClick={() => set_view("dashboard")}
            title="多项目仪表盘"
            style={btnStyle}
          >
            <Icon name="grid" size={13} />
            仪表盘
          </button>
        )}
        <button
          type="button"
          onClick={close_panel}
          title="关闭面板"
          style={{ ...btnStyle, padding: 0, width: 28, height: 28 }}
        >
          <Icon name="close" size={15} />
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
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  gap: 5,
  background: "rgba(255,255,255,0.06)",
  color: "#dbe2f0",
  border: "1px solid rgba(255,255,255,0.12)",
  borderRadius: 8,
  fontSize: 11.5,
  fontWeight: 600,
  padding: "6px 11px",
  cursor: "pointer",
  transition: "background 120ms ease",
};
