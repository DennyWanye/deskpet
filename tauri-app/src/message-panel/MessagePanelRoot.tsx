/**
 * 2026-05-19 — slim message panel as its OWN Tauri window.
 *
 * Why a separate window: when it lived inside the pet window we had to
 * resize the OS window to show/hide it, which caused whitespace / jump
 * / flicker / dead click-blocking area (every fix broke another thing).
 * As an independent always-on-top transparent window docked to the
 * pet's left, the pet window can stay exactly pet-sized & fully
 * transparent (zero dead area), and this panel slides in/out with no
 * pet-window geometry changes at all.
 *
 * Data: this window opens its OWN control WS (the shared code-panel
 * `ws.ts` derives a distinct control session_id from the route hash so
 * the two windows don't kick each other). It already loads + streams
 * the "default" (companion) session, which is exactly what the pet's
 * main thread is — so we just render that session here.
 */
import { invoke } from "@tauri-apps/api/core";

import { useSessionsStore } from "../stores/sessionsStore";
import { MessageStream } from "../code-panel/MessageStream";
import { InputBar } from "../code-panel/InputBar";
// Side-effect import: auto-connects the control WS for THIS window
// (hash → control session_id "message-panel-main") and hydrates the
// "default" session's history + live stream into the store.
import "../code-panel/ws";

export function MessagePanelRoot() {
  const messages = useSessionsStore(
    (s) => s.sessions["default"]?.messages ?? [],
  );

  return (
    <div style={outerStyle}>
      <div style={cardStyle}>
        <header style={headerStyle}>
          <button
            type="button"
            onClick={() =>
              invoke("close_message_panel").catch(() => {
                /* no-op if missing */
              })
            }
            title="收起消息面板"
            aria-label="收起消息面板"
            style={collapseBtnStyle}
          >
            ◀
          </button>
          <span style={{ display: "flex", alignItems: "center", gap: 7 }}>
            <span style={{ fontSize: 14 }}>💬</span>
            <span>消息 · 主线程</span>
          </span>
        </header>
        <div style={{ flex: 1, minHeight: 0, position: "relative" }}>
          <MessageStream messages={messages} />
        </div>
        <InputBar />
      </div>
    </div>
  );
}

const outerStyle: React.CSSProperties = {
  width: "100vw",
  height: "100vh",
  // Tiny margin so the card's rounded corners reveal the desktop —
  // the window itself is transparent, so this looks like a floating
  // glass panel rather than an opaque rectangle.
  padding: 6,
  boxSizing: "border-box",
  background: "transparent",
  overflow: "hidden",
};

const cardStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  width: "100%",
  height: "100%",
  borderRadius: 14,
  overflow: "hidden",
  background:
    "linear-gradient(160deg, rgba(17,21,34,0.95) 0%, rgba(13,16,26,0.93) 55%, rgba(15,23,42,0.95) 100%)",
  border: "1px solid rgba(99,102,241,0.30)",
  boxShadow:
    "0 10px 40px -12px rgba(0,0,0,0.7), inset 0 0 0 1px rgba(148,163,184,0.06)",
  backdropFilter: "blur(14px)",
};

const headerStyle: React.CSSProperties = {
  flexShrink: 0,
  padding: "9px 12px",
  fontSize: 12.5,
  fontWeight: 600,
  letterSpacing: 0.3,
  color: "#c7d2fe",
  display: "flex",
  alignItems: "center",
  justifyContent: "flex-start",
  gap: 8,
  borderBottom: "1px solid rgba(148,163,184,0.12)",
  background:
    "linear-gradient(180deg, rgba(79,70,229,0.18), rgba(79,70,229,0))",
};

const collapseBtnStyle: React.CSSProperties = {
  width: 26,
  height: 26,
  flexShrink: 0,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  background: "rgba(99,102,241,0.20)",
  color: "#c7d2fe",
  border: "1px solid rgba(99,102,241,0.34)",
  borderRadius: 7,
  fontSize: 13,
  cursor: "pointer",
  padding: 0,
};
