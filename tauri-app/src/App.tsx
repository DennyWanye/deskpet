import { useState, useCallback, useEffect, useRef } from "react";
import { Live2DCanvas, type Live2DHandle } from "./components/Live2DCanvas";
import { MemoryPanel } from "./components/MemoryPanel";
import { SettingsPanel } from "./components/SettingsPanel";
import { DialogBar } from "./components/DialogBar";
import { ChatHistoryPanel } from "./components/ChatHistoryPanel";
import { UserBubble } from "./components/UserBubble";
import { useBudgetToast } from "./hooks/useBudgetToast";
import { useControlChannel } from "./hooks/useWebSocket";
import { useAudioChannel } from "./hooks/useAudioChannel";
import { useAudioRecorder } from "./hooks/useAudioRecorder";
import { useAudioPlayer } from "./hooks/useAudioPlayer";
import { useUpdateChecker } from "./hooks/useUpdateChecker";
import { useAutostart } from "./hooks/useAutostart";
import { useBackendLifecycle } from "./hooks/useBackendLifecycle";
import type { AudioMessage, LipSyncMessage } from "./types/messages";

function stripMarkdown(text: string): string {
  return text
    .replace(/\*\*(.*?)\*\*/g, "$1")
    .replace(/\*(.*?)\*/g, "$1")
    .replace(/#{1,6}\s/g, "")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/---+/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function App() {
  // W5 (R17): silent self-update on startup. No-op under dev-browser or
  // when the updater endpoint isn't reachable.
  useUpdateChecker();

  const [fps, setFps] = useState(0);
  const [chatText, setChatText] = useState("");
  // Track whether the backend is routing through cloud or local.
  // "cloud" | "local" | null (unknown)
  const [routeKind, setRouteKind] = useState<"cloud" | "local" | null>(null);
  // Shared secret — fetched from Tauri backend command after it has read the
  // SHARED_SECRET line from the spawned Python process. Empty string while
  // polling; once populated, the WebSocket hooks reconnect with proper auth.
  const [secret, setSecret] = useState("");

  // Poll the Rust side for the shared secret. Extracted so the
  // backend-restarted supervisor event can replay it without duplicating
  // the import + retry loop.
  const refreshSecret = useCallback(async () => {
    const core = await import("@tauri-apps/api/core").catch(() => null);
    if (!core) return;
    for (let i = 0; i < 60; i++) {
      try {
        const s = await core.invoke<string>("get_shared_secret");
        if (s) {
          setSecret(s);
          return;
        }
      } catch {
        // backend not yet up; retry
      }
      await new Promise((r) => setTimeout(r, 500));
    }
  }, []);

  useEffect(() => {
    void refreshSecret();
  }, [refreshSecret]);

  // S12: react to supervisor events — on crash, clear the secret so any
  // active WebSockets see a reconnect cue; on restarted, poll for the
  // new secret and let the WS hooks re-handshake.
  useBackendLifecycle((kind) => {
    if (kind === "crashed") {
      setSecret("");
    } else if (kind === "restarted") {
      void refreshSecret();
    } else if (kind === "dead") {
      console.warn("[backend] supervisor gave up — manual restart required");
    }
  });

  // Autostart toggle (enable run-on-login via plugin-autostart).
  const autostart = useAutostart();
  const [messages, setMessages] = useState<
    { role: "user" | "assistant"; text: string }[]
  >([]);
  const [mouthOpenY, setMouthOpenY] = useState(0);
  const [vadStatus, setVadStatus] = useState<
    "idle" | "listening" | "speaking" | "thinking"
  >("idle");

  // Ref to the Live2D canvas — exposes setExpression/playMotion so control
  // channel events can drive the character directly without re-rendering.
  const liveRef = useRef<Live2DHandle>(null);

  // Control channel (text chat + interrupt + emotion/action events)
  const { state, lastMessage, sendChat, sendInterrupt, getChannel: getControlChannel } =
    useControlChannel(8100, secret);

  // Reset route kind when disconnected.
  useEffect(() => {
    if (state !== "connected") setRouteKind(null);
  }, [state]);

  // S14 — memory management panel toggle.
  const [memoryOpen, setMemoryOpen] = useState(false);

  // P2-1-S3 — settings panel toggle (cloud account / strategy / daily budget).
  const [settingsOpen, setSettingsOpen] = useState(false);

  // P2-1-S8 — budget-exceeded toast. Auto-clears after 6s.
  const [budgetToast, setBudgetToast] = useState<string | null>(null);
  const showBudgetToast = useCallback((msg: string) => {
    setBudgetToast(msg);
  }, []);
  useEffect(() => {
    if (!budgetToast) return;
    const t = setTimeout(() => setBudgetToast(null), 6000);
    return () => clearTimeout(t);
  }, [budgetToast]);
  useBudgetToast(getControlChannel, showBudgetToast);

  // VN 底栏 —— 最新用户输入（驱动 UserBubble 淡出计时）+ 历史面板开关。
  const [latestUserInput, setLatestUserInput] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);

  // Audio channel (voice pipeline)
  const {
    state: audioState,
    lastMessage: audioMessage,
    sendAudio,
    getChannel,
  } = useAudioChannel(8100, secret);

  // Audio recorder (microphone → PCM16 → backend)
  const { isRecording, startRecording, stopRecording } =
    useAudioRecorder(sendAudio);

  // Audio player (backend MP3 → speaker)
  // We buffer MP3 chunks and decode on tts_end — partial MP3 chunks can't be
  // decoded independently (only the first carries the stream header).
  const {
    isPlaying,
    stop: stopPlayback,
    flushAndPlay,
    reset: resetPlaybackBuffer,
    primeContext,
  } = useAudioPlayer(getChannel());

  // Handle control channel messages (text chat + emotion/action drive)
  useEffect(() => {
    if (!lastMessage) return;
    switch (lastMessage.type) {
      case "chat_response":
        setMessages((prev) => [
          ...prev,
          { role: "assistant", text: lastMessage.payload.text },
        ]);
        // Update route indicator based on which provider actually served.
        if (lastMessage.payload.provider) {
          setRouteKind(lastMessage.payload.provider);
        }
        break;
      case "emotion_change":
        // Push named expression to Live2D. Unknown names silently no-op.
        liveRef.current?.setExpression(lastMessage.payload.value);
        break;
      case "action_trigger":
        // Trigger named motion group. Unknown names silently no-op.
        liveRef.current?.playMotion(lastMessage.payload.value);
        break;
    }
  }, [lastMessage]);

  // Handle audio channel JSON messages
  useEffect(() => {
    if (!audioMessage) return;

    switch (audioMessage.type) {
      case "vad_event":
        if (audioMessage.payload.status === "speech_start") {
          setVadStatus("speaking");
          // Barge-in: stop current playback and drop any buffered TTS.
          if (isPlaying) {
            stopPlayback();
            setMouthOpenY(0);
          }
          resetPlaybackBuffer();
        } else {
          setVadStatus("thinking");
        }
        break;

      case "transcript":
        setMessages((prev) => [
          ...prev,
          {
            role: audioMessage.payload.role,
            text: audioMessage.payload.text,
          },
        ]);
        break;

      case "tts_end":
        // Full MP3 has arrived — decode and play the merged blob.
        void flushAndPlay();
        setMouthOpenY(0);
        setVadStatus("listening");
        break;
    }
  }, [audioMessage, isPlaying, stopPlayback, flushAndPlay, resetPlaybackBuffer]);

  // Handle lip-sync from control channel
  useEffect(() => {
    const channel = getChannel();
    if (!channel) return;

    const unsub = channel.onJson((msg: AudioMessage) => {
      if (msg.type === "lip_sync" as string) {
        const lipMsg = msg as unknown as LipSyncMessage;
        setMouthOpenY(lipMsg.payload.amplitude);
      }
    });
    return unsub;
  }, [getChannel]);

  const handleSend = () => {
    if (!chatText.trim()) return;
    setMessages((prev) => [...prev, { role: "user", text: chatText }]);
    // 触发 UserBubble —— 每次用新对象 ref 重置淡出计时，避免相同文本重发时
    // React 因为字符串相等不重置 state（追加零宽空格保证每次 text prop 唯一）。
    setLatestUserInput(chatText + "\u200B".repeat(messages.length));
    sendChat(chatText);
    setChatText("");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // Barge-in: stop local playback + notify backend to cancel in-flight LLM/TTS.
  // Bound to a button (shown while TTS is playing) and to the Escape key.
  const handleInterrupt = useCallback(() => {
    stopPlayback();
    setMouthOpenY(0);
    resetPlaybackBuffer();
    sendInterrupt();
    setVadStatus("idle");
  }, [stopPlayback, resetPlaybackBuffer, sendInterrupt]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isPlaying) {
        handleInterrupt();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isPlaying, handleInterrupt]);

  // Escape 关闭对话历史面板 —— 只在 historyOpen 为真时绑定，和上面的
  // isPlaying-Escape 处理器解耦。两者都只改 state，可以共存：即使同时
  // 触发也只是关闭面板 + 打断 TTS，都是用户按 Esc 合理期待的"停止"语义。
  useEffect(() => {
    if (!historyOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setHistoryOpen(false);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [historyOpen]);

  const toggleRecording = async () => {
    if (isRecording) {
      stopRecording();
      setVadStatus("idle");
    } else {
      // Warm up the AudioContext inside the user-gesture handler so Chrome's
      // autoplay policy allows later `source.start()` to actually emit audio.
      // Creating/resuming the context from a WebSocket onmessage callback
      // instead leaves it "suspended" and playback is silent.
      await primeContext();
      startRecording();
      setVadStatus("listening");
    }
  };

  const handleFpsUpdate = useCallback(
    (newFps: number) => setFps(newFps),
    [],
  );

  // 底栏渲染用 —— 从 messages 里取最后一条 assistant。
  const latestAssistant =
    [...messages].reverse().find((m) => m.role === "assistant")?.text ?? null;

  return (
    <div
      style={{
        width: "100vw",
        height: "100vh",
        backgroundColor: "transparent",
        position: "relative",
        overflow: "hidden",
      }}
    >
      <Live2DCanvas
        ref={liveRef}
        modelPath="/assets/live2d/hiyori/Hiyori.model3.json"
        onFpsUpdate={handleFpsUpdate}
        mouthOpenY={mouthOpenY}
      />

      {/* VN 底栏：只展示最新一条助手回复 */}
      <DialogBar
        latestAssistant={latestAssistant ? stripMarkdown(latestAssistant) : null}
        onOpenHistory={() => setHistoryOpen(true)}
      />

      {/* 用户消息 2s 小气泡 */}
      <UserBubble text={latestUserInput} visibleMs={2000} />

      {/* 完整会话历史（点 💬 按钮展开）*/}
      <ChatHistoryPanel
        open={historyOpen}
        messages={messages.map((m) => ({ role: m.role, text: stripMarkdown(m.text) }))}
        onClose={() => setHistoryOpen(false)}
      />

      <div
        style={{
          position: "absolute",
          bottom: "6px",
          left: "6px",
          right: "6px",
          display: "flex",
          gap: "4px",
          zIndex: 20,
        }}
      >
        {/* Mic button */}
        <button
          data-testid="mic-button"
          onClick={toggleRecording}
          disabled={audioState !== "connected" && state !== "connected"}
          style={{
            width: "32px",
            height: "32px",
            borderRadius: "50%",
            border: "none",
            backgroundColor: isRecording
              ? "#ef4444"
              : vadStatus === "speaking"
                ? "#f59e0b"
                : "#6b7280",
            color: "white",
            fontSize: "14px",
            cursor: "pointer",
            animation: isRecording ? "pulse 1.5s infinite" : "none",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
          title={isRecording ? "Stop recording" : "Start recording"}
        >
          {isRecording ? "⏹" : "🎤"}
        </button>

        {/* Interrupt button — appears only while TTS is playing */}
        {isPlaying && (
          <button
            data-testid="interrupt-button"
            onClick={handleInterrupt}
            style={{
              width: "32px",
              height: "32px",
              borderRadius: "50%",
              border: "none",
              backgroundColor: "#dc2626",
              color: "white",
              fontSize: "14px",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
            title="Interrupt (Esc)"
          >
            ✋
          </button>
        )}

        <input
          data-testid="chat-input"
          type="text"
          value={chatText}
          onChange={(e) => setChatText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            state === "connected" ? "Say something..." : "Connecting..."
          }
          disabled={state !== "connected"}
          style={{
            flex: 1,
            padding: "5px 10px",
            borderRadius: "16px",
            border: "1px solid #ddd",
            fontSize: "12px",
            backgroundColor: "rgba(255,255,255,0.95)",
            outline: "none",
          }}
        />
        <button
          data-testid="send-button"
          onClick={handleSend}
          disabled={state !== "connected" || !chatText.trim()}
          style={{
            padding: "5px 12px",
            borderRadius: "16px",
            border: "none",
            backgroundColor: state === "connected" ? "#3b82f6" : "#ccc",
            color: "white",
            fontSize: "12px",
            cursor: state === "connected" ? "pointer" : "default",
          }}
        >
          Send
        </button>
      </div>

      {/* Status indicators */}
      <div
        style={{
          position: "absolute",
          top: "4px",
          right: "4px",
          display: "flex",
          gap: "6px",
          zIndex: 20,
        }}
      >
        {/* Memory management panel toggle (S14) */}
        <button
          data-testid="memory-toggle"
          onClick={() => setMemoryOpen(true)}
          title="记忆管理"
          style={{
            fontSize: "10px",
            background: "rgba(0,0,0,0.5)",
            color: "white",
            border: "none",
            borderRadius: "4px",
            padding: "2px 6px",
            cursor: "pointer",
          }}
        >
          🗂
        </button>
        {/* Settings panel toggle (P2-1-S3) */}
        <button
          data-testid="settings-toggle"
          onClick={() => setSettingsOpen(true)}
          title="设置"
          style={{
            fontSize: "10px",
            background: "rgba(0,0,0,0.5)",
            color: "white",
            border: "none",
            borderRadius: "4px",
            padding: "2px 6px",
            cursor: "pointer",
          }}
        >
          ⚙
        </button>
        {/* Autostart toggle — only render when the plugin is reachable. */}
        {autostart.ready && (
          <button
            onClick={autostart.toggle}
            title={
              autostart.enabled
                ? "Click to disable run-on-login"
                : "Click to enable run-on-login"
            }
            style={{
              fontSize: "10px",
              background: autostart.enabled ? "#10b981" : "rgba(0,0,0,0.5)",
              color: "white",
              border: "none",
              borderRadius: "4px",
              padding: "2px 6px",
              cursor: "pointer",
            }}
          >
            {autostart.enabled ? "⏻ auto" : "⏻"}
          </button>
        )}
        {vadStatus === "thinking" && !isPlaying && (
          <span
            style={{
              fontSize: "10px",
              color: "#fbbf24",
              backgroundColor: "rgba(0,0,0,0.5)",
              padding: "2px 6px",
              borderRadius: "4px",
            }}
          >
            思考中
          </span>
        )}
        {isPlaying && (
          <span
            style={{
              fontSize: "10px",
              color: "#a78bfa",
              backgroundColor: "rgba(0,0,0,0.5)",
              padding: "2px 6px",
              borderRadius: "4px",
            }}
          >
            TTS
          </span>
        )}
        {isRecording && (
          <span
            style={{
              fontSize: "10px",
              color: "#ef4444",
              backgroundColor: "rgba(0,0,0,0.5)",
              padding: "2px 6px",
              borderRadius: "4px",
            }}
          >
            REC
          </span>
        )}
        <span
          style={{
            fontSize: "10px",
            color: fps >= 30 ? "lime" : "red",
            backgroundColor: "rgba(0,0,0,0.5)",
            padding: "2px 6px",
            borderRadius: "4px",
          }}
        >
          {fps} FPS
        </span>
        <span
          style={{
            fontSize: "10px",
            color:
              state !== "connected"
                ? "orange"
                : routeKind === "cloud"
                  ? "#60a5fa"
                  : routeKind === "local"
                    ? "lime"
                    : "#9ca3af",
            backgroundColor: "rgba(0,0,0,0.5)",
            padding: "2px 6px",
            borderRadius: "4px",
          }}
        >
          {state === "connected" && routeKind
            ? `${routeKind}`
            : state}
        </span>
      </div>

      {/* S14 memory management overlay */}
      <MemoryPanel
        open={memoryOpen}
        onClose={() => setMemoryOpen(false)}
        sessionId="default"
        getChannel={getControlChannel}
      />

      {/* P2-1-S3 settings overlay (cloud / strategy / daily budget) */}
      <SettingsPanel
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        getChannel={getControlChannel}
        lastMessage={lastMessage}
        secret={secret}
        onConfigChanged={() => setRouteKind(null)}
      />

      {/* P2-1-S8 budget-exceeded toast */}
      {budgetToast && (
        <div
          role="status"
          aria-live="polite"
          style={{
            position: "fixed",
            top: 16,
            right: 16,
            maxWidth: 320,
            padding: "10px 14px",
            background: "#b91c1c",
            color: "white",
            borderRadius: 6,
            fontSize: 13,
            boxShadow: "0 4px 12px rgba(0,0,0,0.25)",
            zIndex: 2000,
          }}
        >
          {budgetToast}
        </div>
      )}

      {/* Pulse animation for recording button */}
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.7; transform: scale(1.1); }
        }
      `}</style>
    </div>
  );
}

export default App;
