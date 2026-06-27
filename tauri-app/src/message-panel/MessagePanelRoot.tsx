// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

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
import { useMemo, useState, useEffect, useCallback, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";

import { Icon } from "../components/Icon";
import { ContextRing } from "../components/ContextRing";
import { ContextBreakdownModal } from "../components/ContextBreakdownModal";

import {
  useSessionsStore,
  collect_inbox,
  type InboxItem,
} from "../stores/sessionsStore";
import { forPet } from "../petText";
import {
  MessageStreamPanel,
  type ChatStreamMessage,
  type StreamFilter,
} from "../components/MessageStreamPanel";
import { InputBar } from "../code-panel/InputBar";
import { ChangeModelModal } from "../code-panel/ChangeModelModal";
import {
  useCodeModelsStore,
  contextWindowForModel,
  formatContextWindow,
  effectiveModelId,
} from "../code-panel/codeModelsStore";
import { codePanelWS } from "../code-panel/ws";
import { useAudioChannel } from "../hooks/useAudioChannel";
import { BACKEND_PORT } from "../backendPort";
import { useAudioRecorder } from "../hooks/useAudioRecorder";
import { useAudioPlayer } from "../hooks/useAudioPlayer";

const DEFAULT_SID = "default"; // the pet's companion main thread

type SessionEntry = {
  session_id: string;
  turn_count: number;
  last_message_at: number;
  preview: string;
};

export function MessagePanelRoot() {
  const [activeSid, setActiveSid] = useState(DEFAULT_SID);
  const [filter, setFilter] = useState<StreamFilter>("all");
  // 历史会话下拉（选择 / 删除之前的会话）。
  const [sessionList, setSessionList] = useState<SessionEntry[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [showModelModal, setShowModelModal] = useState(false);
  // 2026-05-31 restore — context breakdown modal state + snapshot subscriber.
  const [contextModalOpen, setContextModalOpen] = useState(false);
  const contextUsage = useSessionsStore((s) => s.sessions[activeSid]?.context_usage ?? null);

  const sessions = useSessionsStore((s) => s.sessions);
  const messages = useSessionsStore((s) => s.sessions[activeSid]?.messages ?? []);
  const preferred_model = useSessionsStore(
    (s) => s.sessions[activeSid]?.preferred_model ?? null,
  );
  const model_params = useSessionsStore(
    (s) => s.sessions[activeSid]?.model_params ?? null,
  );
  // 模型按钮显示「模型-上下文长度(K/M)」。未固定 preferred_model 时,显示
  // 生效的 provider 默认模型(后端经 code_models_list_response 下发),让用户
  // 看到「当前真正在用的模型」而非「默认模型」占位词。
  const modelCatalog = useCodeModelsStore((s) => s.models);
  const default_model = useCodeModelsStore((s) => s.default_model);
  const eff_model = effectiveModelId(preferred_model, default_model);
  const is_following_default = !((preferred_model ?? "").trim());
  const ctx_window = contextWindowForModel(eff_model, modelCatalog);
  const ctx_label = formatContextWindow(ctx_window);

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

  useEffect(() => {
    return codePanelWS.on_message((msg: any) => {
      if (msg?.type !== "session_switched" && msg?.type !== "task_session_started") {
        return;
      }
      const nextSid = msg?.payload?.new_sid;
      if (typeof nextSid !== "string" || !nextSid) return;
      useSessionsStore.getState().ensure(nextSid);
      setActiveSid(nextSid);
    });
  }, []);

  const switchToDefault = useCallback(() => {
    useSessionsStore.getState().ensure(DEFAULT_SID);
    useSessionsStore.getState().set_active(DEFAULT_SID);
    setActiveSid(DEFAULT_SID);
  }, []);

  // 历史会话下拉：拉清单 / 切会话 / 删会话。
  const loadSessions = useCallback(() => {
    codePanelWS.send({ type: "sessions_list" });
  }, []);

  const switchToSession = useCallback((sid: string) => {
    useSessionsStore.getState().ensure(sid);
    useSessionsStore.getState().set_active(sid);
    setActiveSid(sid);
    // 拉该会话历史回灌 store（ws.ts 的 session_messages_response → set_messages）。
    codePanelWS.send({
      type: "session_messages_load",
      payload: { session_id: sid, limit: 200 },
    });
    setPickerOpen(false);
  }, []);

  const deleteSession = useCallback(
    (sid: string) => {
      codePanelWS.send({ type: "session_delete", payload: { session_id: sid } });
      // 乐观移除 + 若删的是当前会话则切回 default。
      setSessionList((prev) => prev.filter((s) => s.session_id !== sid));
      if (sid === activeSid && sid !== DEFAULT_SID) {
        switchToDefault();
      }
    },
    [activeSid, switchToDefault],
  );

  // 监听后端 sessions_list_response / session_deleted；面板打开时拉一次清单。
  useEffect(() => {
    const off = codePanelWS.on_message((msg: any) => {
      if (msg?.type === "sessions_list_response") {
        const arr = Array.isArray(msg?.payload?.sessions) ? msg.payload.sessions : [];
        setSessionList(arr);
      } else if (msg?.type === "session_deleted") {
        // 后端确认删除 → 重新拉清单保持一致。
        loadSessions();
      }
    });
    loadSessions();
    return off;
  }, [loadSessions]);

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
        useSessionsStore.getState().push_message(activeSid, {
          role: audioMessage.payload.role,
          text: audioMessage.payload.text,
        });
        break;
      case "tts_barge_in":
        bargeIn();
        break;
    }
  }, [audioMessage, isPlaying, resetPlaybackBuffer, bargeIn, activeSid]);

  // Companion-stream derivation (strip <think> via forPet; synth ts
  // since the store has none)。2026-06-12: 工具执行轨迹(tool_call/
  // tool_result)不再丢弃 —— 用户要求「我让它生成PPT 和 它生成完之间
  // 的工具调用」在主消息流全程可观测(此前只有桌宠小气泡显示)。
  // 「隐藏工具消息」开关: 开=只看对话(隐藏 🔧/✅ 工具轨迹行),
  // 关=全程可观测。localStorage 持久化,重开面板记住选择。
  const [hideTools, setHideTools] = useState<boolean>(
    () => {
      try {
        return localStorage.getItem("msgpanel.hideTools") === "1";
      } catch {
        return false;
      }
    },
  );
  const toggleHideTools = () => {
    setHideTools((v) => {
      const next = !v;
      try {
        localStorage.setItem("msgpanel.hideTools", next ? "1" : "0");
      } catch { /* 忽略 */ }
      return next;
    });
  };

  const chatMessages = useMemo(() => {
    const out: ChatStreamMessage[] = [];
    messages.forEach((m, i) => {
      const ts = Date.now() - (messages.length - i) * 1000;
      if (m.role === "user") {
        out.push({ role: "user", text: m.text ?? "", ts });
        return;
      }
      if (hideTools && ((m.role as string) === "tool_call" || (m.role as string) === "tool_result")) {
        return;
      }
      if ((m.role as string) === "tool_call") {
        const args = m.tool_args
          ? JSON.stringify(m.tool_args).slice(0, 120)
          : "";
        out.push({
          role: "tool",
          text: `🔧 调用 ${m.tool_name || "(工具)"}${args ? ` ${args}${args.length >= 120 ? "…" : ""}` : ""}`,
          ts,
        });
        return;
      }
      if ((m.role as string) === "tool_result") {
        out.push({
          role: "tool",
          text: `${m.tool_ok === false ? "❌" : "✅"} ${m.tool_name || "(工具)"} 完成`,
          ts,
        });
        return;
      }
      if (m.role === "ppt_outline") {
        out.push({ role: "ppt_outline", message: m, session_id: activeSid, ts });
        return;
      }
      const clean = forPet(m.text);
      if (clean) out.push({ role: "assistant", text: clean, ts });
    });
    return out;
  }, [messages, hideTools, activeSid]);
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

  // Drag the frameless window via startDragging() — the idiomatic Windows
  // move-loop API, robust to the cursor leaving the window, multi-monitor & DPI.
  //
  // 2026-06-26 修复"拖动不行"(回归)：原实现在 onMouseDown 里**异步** `import(
  // "@tauri-apps/api/window")` 再调 startDragging() —— 动态 import 的 promise 要到
  // 下一个 microtask 才 resolve，而 Windows 的 SC_MOVE 移动循环**必须在 mousedown
  // 同步帧内**发起才能接管拖动；晚一拍 → OS 不进入移动循环 → 拖不动。改为像
  // Live2DCanvas(FIX-R3) 一样**预加载** startDragging 到 ref，onMouseDown 里**同步**调用。
  const startDraggingRef = useRef<(() => Promise<unknown>) | null>(null);
  useEffect(() => {
    let alive = true;
    import("@tauri-apps/api/window")
      .then(({ getCurrentWindow }) => {
        if (!alive) return;
        const w = getCurrentWindow();
        startDraggingRef.current = () => w.startDragging();
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);
  const startDrag = (e: React.MouseEvent) => {
    if (e.button !== 0) return;
    // 同步调用预加载好的 startDragging（晚一拍就拖不动，见上方注释）。
    startDraggingRef.current?.().catch((err) =>
      console.warn("[msg-panel] startDragging failed:", err),
    );
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
          {/* 历史会话选择器：点标题展开下拉，列出之前的会话，可切换 / 删除(每项 ×)。 */}
          <div
            onMouseDown={(e) => e.stopPropagation()}
            style={{ position: "relative", flex: 1, minWidth: 0 }}
          >
            <button
              type="button"
              onClick={() => {
                if (!pickerOpen) loadSessions();
                setPickerOpen((v) => !v);
              }}
              title="选择历史会话"
              style={{
                display: "flex",
                alignItems: "center",
                gap: 7,
                width: "100%",
                minWidth: 0,
                background: "transparent",
                border: "none",
                color: "inherit",
                font: "inherit",
                cursor: "pointer",
                padding: 0,
              }}
            >
              <Icon name="message" size={14} style={{ color: "#a5b4fc" }} />
              <span
                style={{
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  flex: 1,
                  textAlign: "left",
                }}
              >
                {activeSid === DEFAULT_SID ? "消息 · 默认话题" : `消息 · ${activeSid}`}
              </span>
              <span style={{ flexShrink: 0, opacity: 0.7, fontSize: 10 }}>▾</span>
            </button>
            {pickerOpen && (
              <>
                {/* 点空白处关闭 */}
                <div
                  onClick={() => setPickerOpen(false)}
                  style={{ position: "fixed", inset: 0, zIndex: 998 }}
                />
                <div
                  style={{
                    position: "absolute",
                    top: "100%",
                    left: 0,
                    marginTop: 6,
                    minWidth: 260,
                    maxWidth: 360,
                    maxHeight: 340,
                    overflowY: "auto",
                    background: "rgba(20,24,36,0.99)",
                    border: "1px solid rgba(148,163,184,0.3)",
                    borderRadius: 10,
                    boxShadow: "0 8px 28px rgba(0,0,0,0.5)",
                    zIndex: 999,
                    padding: 4,
                  }}
                >
                  {sessionList.length === 0 && (
                    <div style={{ padding: "10px 12px", color: "#64748b", fontSize: 12 }}>
                      暂无历史会话
                    </div>
                  )}
                  {sessionList.map((s) => {
                    const selected = s.session_id === activeSid;
                    const isDefault = s.session_id === DEFAULT_SID;
                    const label = isDefault
                      ? "默认话题"
                      : s.preview || s.session_id;
                    return (
                      <div
                        key={s.session_id}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 6,
                          padding: "7px 8px",
                          borderRadius: 7,
                          background: selected
                            ? "rgba(37,99,235,0.22)"
                            : "transparent",
                          borderLeft: selected
                            ? "3px solid #60a5fa"
                            : "3px solid transparent",
                        }}
                      >
                        <div
                          onClick={() => switchToSession(s.session_id)}
                          style={{ flex: 1, minWidth: 0, cursor: "pointer" }}
                        >
                          <div
                            style={{
                              color: selected ? "#bfdbfe" : "#e2e8f0",
                              fontSize: 13,
                              fontWeight: selected ? 600 : 500,
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                            }}
                          >
                            {label}
                          </div>
                          <div style={{ color: "#94a3b8", fontSize: 11, marginTop: 1 }}>
                            {s.turn_count} 条
                            {!isDefault && ` · ${s.session_id}`}
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            deleteSession(s.session_id);
                          }}
                          title={isDefault ? "清空默认话题" : "删除该会话"}
                          aria-label="删除该会话"
                          style={{
                            flexShrink: 0,
                            width: 22,
                            height: 22,
                            borderRadius: 6,
                            border: "none",
                            background: "transparent",
                            color: "#94a3b8",
                            cursor: "pointer",
                            fontSize: 15,
                            lineHeight: "20px",
                          }}
                          onMouseEnter={(e) => {
                            e.currentTarget.style.background = "rgba(239,68,68,0.18)";
                            e.currentTarget.style.color = "#f87171";
                          }}
                          onMouseLeave={(e) => {
                            e.currentTarget.style.background = "transparent";
                            e.currentTarget.style.color = "#94a3b8";
                          }}
                        >
                          ×
                        </button>
                      </div>
                    );
                  })}
                </div>
              </>
            )}
          </div>
          <button
            type="button"
            onMouseDown={(e) => e.stopPropagation()}
            onClick={() => setShowModelModal(true)}
            title={
              eff_model
                ? is_following_default
                  ? `模型与参数（当前 ${eff_model} · 跟随 provider 默认）`
                  : `模型与参数（当前 ${eff_model}）`
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
              {eff_model
                ? ctx_label
                  ? `${eff_model}-${ctx_label}`
                  : eff_model
                : "默认模型"}
            </span>
            <Icon name="edit" size={11} style={{ flexShrink: 0 }} />
          </button>
          {/* 隐藏/显示工具消息(🔧 调用轨迹行) */}
          <button
            type="button"
            onMouseDown={(e) => e.stopPropagation()}
            onClick={toggleHideTools}
            title={hideTools ? "显示工具消息" : "隐藏工具消息"}
            aria-label={hideTools ? "显示工具消息" : "隐藏工具消息"}
            aria-pressed={hideTools}
            style={{
              ...iconBtnStyle,
              color: hideTools ? "#64748b" : "#67e8f9",
            }}
          >
            <span style={{ fontSize: 12, lineHeight: 1 }}>🔧</span>
          </button>
          {/* 2026-05-31 restore — context ring in header */}
          <span
            onMouseDown={(e) => e.stopPropagation()}
            style={{ display: "inline-flex", alignItems: "center" }}
          >
            <ContextRing
              snapshot={contextUsage}
              size={18}
              showLabel
              onClick={() => setContextModalOpen(true)}
            />
          </span>
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
          sessionId={activeSid}
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
          session_id={activeSid}
          current_model={preferred_model}
          current_params={model_params}
          onClose={() => setShowModelModal(false)}
        />
      )}

      {/* 2026-05-31 restore — context-usage breakdown modal */}
      <ContextBreakdownModal
        open={contextModalOpen}
        onClose={() => setContextModalOpen(false)}
        sessionId={activeSid}
        snapshot={contextUsage}
        send={(m) => codePanelWS.send(m)}
        onMessage={(fn) => codePanelWS.on_message(fn)}
      />

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
  // 极简：扁平双段深色背景，去掉多段渐变与内高光噪点。
  background:
    "linear-gradient(180deg, rgba(22,26,40,0.97) 0%, rgba(15,17,26,0.98) 100%)",
  border: "1px solid rgba(255,255,255,0.07)",
  boxShadow: "0 16px 44px rgba(0,0,0,0.55)",
  backdropFilter: "blur(22px) saturate(1.4)",
  WebkitBackdropFilter: "blur(22px) saturate(1.4)",
};

const headerStyle: React.CSSProperties = {
  flexShrink: 0,
  padding: "11px 14px",
  fontSize: 13,
  fontWeight: 600,
  letterSpacing: 0.2,
  color: "#e8edf6",
  display: "flex",
  alignItems: "center",
  gap: 8,
  borderBottom: "1px solid rgba(255,255,255,0.05)",
  // 极简：去掉靛蓝渐变，扁平透明，靠分隔线区分。
  background: "transparent",
};

// 统一图标按钮：扁平、低对比、一致尺寸（28），无重边框。
const iconBtnStyle: React.CSSProperties = {
  width: 28,
  height: 28,
  flexShrink: 0,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  background: "rgba(255,255,255,0.045)",
  color: "#c7d2fe",
  border: "1px solid rgba(255,255,255,0.07)",
  borderRadius: 9,
  cursor: "pointer",
  padding: 0,
};

const modelChipStyle: React.CSSProperties = {
  flexShrink: 0,
  maxWidth: 150,
  height: 28,
  padding: "0 11px",
  display: "flex",
  alignItems: "center",
  gap: 5,
  // 与图标按钮同款低对比底色，保持工整一致（不再单独用靛蓝高亮）。
  background: "rgba(255,255,255,0.045)",
  color: "#c7d2fe",
  border: "1px solid rgba(255,255,255,0.08)",
  borderRadius: 999,
  fontSize: 11,
  fontWeight: 600,
  cursor: "pointer",
};
