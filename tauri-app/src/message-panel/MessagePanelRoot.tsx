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
import { useMemo, useState, useEffect, useCallback } from "react";
import { invoke } from "@tauri-apps/api/core";

import { Icon } from "../components/Icon";

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
import { useAudioChannel } from "../hooks/useAudioChannel";
import { BACKEND_PORT } from "../backendPort";
import { useAudioRecorder } from "../hooks/useAudioRecorder";
import { useAudioPlayer } from "../hooks/useAudioPlayer";

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

  // ── Voice pipeline (parity with the pet's main mic) ──────────────
  // The panel is its own window, so it runs its own audio channel +
  // recorder + player. It connects with the default session_id, so the
  // backend persists voice turns to the SAME "default" session the
  // pet main + panel text use. transcript/TTS come back over THIS
  // window's audio_ws → we echo transcripts into the store so the
  // voice exchange shows in the panel, consistent with text.
  const [secret, setSecret] = useState("");
  useEffect(() => {
    let alive = true;
    let tries = 0;
    const poll = async () => {
      try {
        const s = await invoke<string>("get_shared_secret");
        if (alive && s) {
          setSecret(s);
          return;
        }
      } catch {
        /* backend not up yet */
      }
      if (alive && tries++ < 40) setTimeout(poll, 500);
    };
    void poll();
    return () => {
      alive = false;
    };
  }, []);

  const {
    state: audioState,
    lastMessage: audioMessage,
    sendAudio,
    getChannel,
  } = useAudioChannel(BACKEND_PORT, secret);
  const { isRecording, startRecording, stopRecording } =
    useAudioRecorder(sendAudio);
  const {
    isPlaying,
    reset: resetPlaybackBuffer,
    primeContext,
    bargeIn,
  } = useAudioPlayer(getChannel());

  const toggleRecording = useCallback(async () => {
    if (isRecording) {
      stopRecording();
    } else {
      await primeContext(); // unlock AudioContext inside the gesture
      startRecording();
    }
  }, [isRecording, startRecording, stopRecording, primeContext]);

  // Mirror App.tsx's audio-message handling, but echo transcripts into
  // the shared store so MessageStreamPanel renders the voice turns.
  useEffect(() => {
    if (!audioMessage) return;
    switch (audioMessage.type) {
      case "vad_event":
        if (audioMessage.payload.status === "speech_start") {
          if (isPlaying) bargeIn();
          resetPlaybackBuffer();
        }
        break;
      case "transcript":
        useSessionsStore.getState().push_message(SID, {
          role: audioMessage.payload.role,
          text: audioMessage.payload.text,
        });
        break;
      case "tts_barge_in":
        bargeIn();
        break;
    }
  }, [audioMessage, isPlaying, resetPlaybackBuffer, bargeIn]);

  // Same companion-stream derivation as App.tsx (strip <think>/tool
  // trace via forPet; synth ts since the store has none).
  const chatMessages = useMemo(() => {
    type ChatItem = { role: "user" | "assistant"; text: string; ts: number };
    const out: ChatItem[] = [];
    messages.forEach((m, i) => {
      const ts = Date.now() - (messages.length - i) * 1000;
      if (m.role === "user") {
        out.push({ role: "user", text: m.text ?? "", ts });
        return;
      }
      const clean = forPet(m.text);
      if (clean) out.push({ role: "assistant", text: clean, ts });
    });
    return out;
  }, [messages]);
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

  // Drag the frameless window. The ACTUAL bug behind "拖动不行" was a
  // missing Tauri capability: `message-panel` was absent from
  // capabilities/default.json's `windows` list, so EVERY core:window
  // IPC (start-dragging, set-position, …) from this window was denied
  // and silently swallowed. With the window now in the capability,
  // startDragging() — the idiomatic Windows move-loop API, robust to
  // the cursor leaving the moving window, multi-monitor and DPI — works
  // for a real mouse. (data-tauri-drag-region kept as a harmless extra
  // hint.) Left button only; header buttons stopPropagation so their
  // own clicks never start a drag.
  const startDrag = (e: React.MouseEvent) => {
    if (e.button !== 0) return;
    import("@tauri-apps/api/window")
      .then(({ getCurrentWindow }) => getCurrentWindow().startDragging())
      .catch((err) => console.warn("[msg-panel] startDragging failed:", err));
  };

  const toggleMaximize = async () => {
    try {
      const mod = await import("@tauri-apps/api/window");
      const w = mod.getCurrentWindow?.();
      if (!w) return;
      // toggleMaximize() no-ops on this window type — drive size
      // explicitly: fullscreen toggle, falling back gracefully.
      const isFs = await w.isFullscreen();
      await w.setFullscreen(!isFs);
    } catch (e) {
      console.warn("[msg-panel] toggleMaximize failed:", e);
    }
  };

  return (
    <div style={outerStyle}>
      <div style={cardStyle}>
        {/* Header = drag region (move the window). Buttons stop the
            drag so clicks register. */}
        <header
          style={headerStyle}
          data-tauri-drag-region
          onMouseDown={startDrag}
        >
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
            <Icon name="chevron-left" size={14} />
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
            <Icon name="message" size={14} style={{ color: "#a5b4fc" }} />
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
            <span
              style={{
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {preferred_model || "默认模型"}
            </span>
            <Icon name="edit" size={11} style={{ flexShrink: 0 }} />
          </button>
          <button
            type="button"
            onMouseDown={(e) => e.stopPropagation()}
            onClick={() => void toggleMaximize()}
            title="放大 / 还原"
            aria-label="放大 / 还原"
            style={iconBtnStyle}
          >
            <Icon name="expand" size={13} />
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

        {/* Same companion chat_v2 path as the pet's main input —
            sessionId="default" pins send/echo/stop to the SAME session
            the pet main uses and MessageStreamPanel renders.
            leftAccessory = the mic button, parity with the pet bar. */}
        <InputBar
          placeholder="和桌宠说点什么…"
          sessionId={SID}
          leftAccessory={
            <button
              type="button"
              onClick={() => void toggleRecording()}
              disabled={audioState !== "connected"}
              title={
                audioState !== "connected"
                  ? "语音通道连接中…"
                  : isRecording
                    ? "停止录音"
                    : "按住说话"
              }
              aria-label={isRecording ? "停止录音" : "语音输入"}
              style={{
                width: 36,
                height: 36,
                flexShrink: 0,
                borderRadius: "50%",
                border: `1px solid ${
                  isRecording
                    ? "rgba(239,68,68,0.55)"
                    : "rgba(255,255,255,0.12)"
                }`,
                background: isRecording
                  ? "linear-gradient(180deg,#f87171,#ef4444)"
                  : audioState === "connected"
                    ? "rgba(129,140,248,0.20)"
                    : "rgba(255,255,255,0.06)",
                color: isRecording ? "#fff" : "#c7d2fe",
                cursor:
                  audioState === "connected" ? "pointer" : "not-allowed",
                boxShadow: isRecording ? "0 0 13px rgba(239,68,68,0.5)" : "none",
                animation: isRecording ? "pulse 1.5s infinite" : "none",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <Icon name={isRecording ? "stop" : "mic"} size={16} />
            </button>
          }
        />
      </div>

      {showModelModal && (
        <ChangeModelModal
          session_id={SID}
          current_model={preferred_model}
          current_params={model_params}
          onClose={() => setShowModelModal(false)}
        />
      )}

      {/* Recording-button pulse — this window has its own DOM, so it
          needs its own copy of the keyframes (App's is pet-window only). */}
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.7; transform: scale(1.1); }
        }
      `}</style>
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
  borderRadius: 16,
  overflow: "hidden",
  background:
    "linear-gradient(165deg, rgba(26,30,46,0.96) 0%, rgba(15,17,26,0.97) 60%, rgba(17,20,34,0.97) 100%)",
  border: "1px solid rgba(255,255,255,0.09)",
  boxShadow:
    "0 18px 50px rgba(0,0,0,0.62), inset 0 1px 0 rgba(255,255,255,0.07)",
  backdropFilter: "blur(22px) saturate(1.5)",
  WebkitBackdropFilter: "blur(22px) saturate(1.5)",
};

const headerStyle: React.CSSProperties = {
  flexShrink: 0,
  padding: "10px 12px",
  fontSize: 12.5,
  fontWeight: 600,
  letterSpacing: 0.3,
  color: "#dbe2f0",
  display: "flex",
  alignItems: "center",
  gap: 8,
  borderBottom: "1px solid rgba(255,255,255,0.06)",
  background:
    "linear-gradient(180deg, rgba(99,102,241,0.16), rgba(99,102,241,0))",
};

const iconBtnStyle: React.CSSProperties = {
  width: 27,
  height: 27,
  flexShrink: 0,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  background: "rgba(255,255,255,0.06)",
  color: "#c7d2fe",
  border: "1px solid rgba(255,255,255,0.12)",
  borderRadius: 8,
  cursor: "pointer",
  padding: 0,
};

const modelChipStyle: React.CSSProperties = {
  flexShrink: 0,
  maxWidth: 150,
  height: 27,
  padding: "0 9px",
  display: "flex",
  alignItems: "center",
  gap: 5,
  background: "rgba(99,102,241,0.16)",
  color: "#c7d2fe",
  border: "1px solid rgba(129,140,248,0.34)",
  borderRadius: 999,
  fontSize: 10.5,
  fontWeight: 600,
  cursor: "pointer",
};
