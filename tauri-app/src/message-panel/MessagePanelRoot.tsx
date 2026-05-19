/**
 * 2026-05-19 — slim message panel as its OWN Tauri window, docked to
 * the pet's left. Independent always-on-top transparent window so the
 * pet window stays exactly pet-sized & fully transparent (zero dead
 * click area).
 *
 * Parity with the pet's main thread (user requests #3/#5/#6):
 *  · full MessageStreamPanel — 全部 / 对话 / ⚠ 警告 / 🚨 错误 tabs,
 *    fed by THIS window's own store ("default" companion session +
 *    supervisor inbox), same derivation as App.tsx.
 *  · InputBar wired to the default companion session (same backend
 *    chat_v2 path the pet's main input uses).
 *  · model + params switcher (the Cursor-style ChangeModelModal) for
 *    the current session, exactly like Code mode.
 *  · header is a drag region; ⛶ maximizes / restores the window.
 */
import { useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

import {
  useSessionsStore,
  collect_inbox,
  type InboxItem,
} from "../stores/sessionsStore";
import { forPet } from "../petText";
import {
  MessageStreamPanel,
  type StreamFilter,
} from "../components/MessageStreamPanel";
import { InputBar } from "../code-panel/InputBar";
import { ChangeModelModal } from "../code-panel/ChangeModelModal";
import { codePanelWS } from "../code-panel/ws";

const SID = "default"; // the pet's companion main thread

export function MessagePanelRoot() {
  const [filter, setFilter] = useState<StreamFilter>("all");
  const [showModelModal, setShowModelModal] = useState(false);

  const sessions = useSessionsStore((s) => s.sessions);
  const messages = useSessionsStore((s) => s.sessions[SID]?.messages ?? []);
  const preferred_model = useSessionsStore(
    (s) => s.sessions[SID]?.preferred_model ?? null,
  );
  const model_params = useSessionsStore(
    (s) => s.sessions[SID]?.model_params ?? null,
  );

  // Same companion-stream derivation as App.tsx (strip <think>/tool
  // trace via forPet; synth ts since the store has none).
  const chatMessages = useMemo(
    () =>
      messages.flatMap((m, i) => {
        const ts = Date.now() - (messages.length - i) * 1000;
        if (m.role === "user") {
          return [{ role: "user" as const, text: m.text ?? "", ts }];
        }
        const clean = forPet(m.text);
        if (!clean) return [];
        return [{ role: "assistant" as const, text: clean, ts }];
      }),
    [messages],
  );
  const warnings = useMemo<InboxItem[]>(
    () => collect_inbox(sessions, "yellow"),
    [sessions],
  );
  const errors = useMemo<InboxItem[]>(
    () => collect_inbox(sessions, "red"),
    [sessions],
  );

  const onChoice = (
    sid: string,
    alert_id: string,
    button_index: number,
    button_text: string,
  ) => {
    codePanelWS.send({
      type: "supervisor_user_choice",
      payload: { session_id: sid, alert_id, button_index, button_text },
    });
    useSessionsStore.getState().dismiss_alert(sid, alert_id);
  };

  const toggleMaximize = async () => {
    try {
      const mod = await import("@tauri-apps/api/window");
      const w = mod.getCurrentWindow?.();
      if (w) await w.toggleMaximize();
    } catch (e) {
      console.warn("[msg-panel] toggleMaximize failed:", e);
    }
  };

  return (
    <div style={outerStyle}>
      <div style={cardStyle}>
        {/* Header = drag region (move the window). Buttons stop the
            drag so clicks register. */}
        <header style={headerStyle} data-tauri-drag-region>
          <button
            type="button"
            onMouseDown={(e) => e.stopPropagation()}
            onClick={() =>
              invoke("close_message_panel").catch(() => {})
            }
            title="收起消息面板"
            aria-label="收起消息面板"
            style={iconBtnStyle}
          >
            ◀
          </button>
          <span
            style={{
              display: "flex",
              alignItems: "center",
              gap: 7,
              flex: 1,
              pointerEvents: "none",
            }}
          >
            <span style={{ fontSize: 14 }}>💬</span>
            <span>消息 · 主线程</span>
          </span>
          <button
            type="button"
            onMouseDown={(e) => e.stopPropagation()}
            onClick={() => setShowModelModal(true)}
            title={
              preferred_model
                ? `模型与参数（当前 ${preferred_model}）`
                : "选择模型与参数"
            }
            aria-label="模型与参数"
            style={modelChipStyle}
          >
            {preferred_model ? `${preferred_model} ✎` : "默认模型 ✎"}
          </button>
          <button
            type="button"
            onMouseDown={(e) => e.stopPropagation()}
            onClick={() => void toggleMaximize()}
            title="放大 / 还原"
            aria-label="放大 / 还原"
            style={iconBtnStyle}
          >
            ⛶
          </button>
        </header>

        <div style={{ flex: 1, minHeight: 0, position: "relative" }}>
          <MessageStreamPanel
            embedded
            filter={filter}
            chatMessages={chatMessages}
            warnings={warnings}
            errors={errors}
            onSetFilter={setFilter}
            onDismiss={(sid, id) =>
              useSessionsStore.getState().dismiss_alert(sid, id)
            }
            onDismissAll={(sev) =>
              useSessionsStore.getState().dismiss_all_alerts(sev)
            }
            onJumpToSession={() => {
              /* single-thread panel — nothing to jump to */
            }}
            onChoice={onChoice}
          />
        </div>

        {/* Same companion chat_v2 path as the pet's main input. */}
        <InputBar placeholder="和桌宠说点什么…" />
      </div>

      {showModelModal && (
        <ChangeModelModal
          session_id={SID}
          current_model={preferred_model}
          current_params={model_params}
          onClose={() => setShowModelModal(false)}
        />
      )}
    </div>
  );
}

const outerStyle: React.CSSProperties = {
  width: "100vw",
  height: "100vh",
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
  gap: 8,
  borderBottom: "1px solid rgba(148,163,184,0.12)",
  background:
    "linear-gradient(180deg, rgba(79,70,229,0.18), rgba(79,70,229,0))",
};

const iconBtnStyle: React.CSSProperties = {
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

const modelChipStyle: React.CSSProperties = {
  flexShrink: 0,
  maxWidth: 150,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  height: 26,
  padding: "0 8px",
  display: "flex",
  alignItems: "center",
  background: preferredChipBg(),
  color: "#cbd5e1",
  border: "1px solid rgba(96,165,250,0.35)",
  borderRadius: 7,
  fontSize: 10.5,
  cursor: "pointer",
};

function preferredChipBg(): string {
  return "rgba(37, 99, 235, 0.20)";
}
