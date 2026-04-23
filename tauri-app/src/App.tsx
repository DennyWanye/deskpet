import { useState, useCallback, useEffect, useRef } from "react";
import { Live2DCanvas, type Live2DHandle } from "./components/Live2DCanvas";
import { MemoryPanel } from "./components/MemoryPanel";
import { SettingsPanel } from "./components/SettingsPanel";
import { DialogBar } from "./components/DialogBar";
import { ChatHistoryPanel } from "./components/ChatHistoryPanel";
import { UserBubble } from "./components/UserBubble";
import { StartupOverlay, type BootState } from "./components/StartupOverlay";
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

  // P3-S8 — visible startup state so users see a spinner / actionable
  // error instead of a silent black transparent window.
  const [bootState, setBootState] = useState<BootState>("starting");
  const [bootError, setBootError] = useState<string | null>(null);
  const [bootAttempt, setBootAttempt] = useState(0);

  // Poll the Rust side for the shared secret. Pure polling — no side
  // effects on the backend process. Safe to replay on HMR, F5, and the
  // backend-restarted supervisor event.
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

  // Bootstrap Python backend. Rust 的 start_backend 现在幂等 —— 用
  // shared_secret 而非 state.child 做判据，所以 F5 / StrictMode / HMR
  // 场景下重复触发只会返回现任 secret，不会抢端口 spawn 第二条 Python。
  // 正是因为幂等，前端这里无需 useRef 守卫也无需 "先查后启" 两段式
  // 逻辑，直接 invoke 即可。
  //
  // P3-S3: backend path 不再由前端传 —— Rust 侧 backend_launch::resolve
  // 按 bundle → env → dev-fallback 优先级自己定位，打包版走 Bundled
  // exe，dev 走 DESKPET_DEV_ROOT。前端 invoke 无参。
  useEffect(() => {
    (async () => {
      const core = await import("@tauri-apps/api/core").catch(() => null);
      if (!core) {
        // Not inside Tauri (e.g. vite dev browser preview) — skip boot
        // state machine entirely so the app still loads for UI work.
        setBootState("ready");
        return;
      }
      setBootState("starting");
      setBootError(null);
      try {
        const secret = await core.invoke<string>("start_backend");
        if (secret) {
          setSecret(secret);
          setBootState("ready");
          return;
        }
        // Empty-secret success is an unexpected branch (Rust always
        // returns Err on timeout now); fall through to error state.
        setBootError("Backend returned an empty SHARED_SECRET");
        setBootState("failed");
      } catch (e) {
        const msg = typeof e === "string" ? e : (e as Error)?.message ?? String(e);
        console.warn("[bootstrap] start_backend failed:", msg);
        // Also peek at Rust's cached error (richer if spawn_once tripped
        // port-in-use or SHARED_SECRET timeout) — prefer that message.
        try {
          const cached = await core.invoke<string | null>("get_startup_error");
          setBootError(cached || msg);
        } catch {
          setBootError(msg);
        }
        setBootState("failed");
      }
    })();
  }, [bootAttempt]);

  // P3-S8 — handlers bound to the startup error card buttons.
  const handleBootRetry = useCallback(async () => {
    const core = await import("@tauri-apps/api/core").catch(() => null);
    if (core) {
      try {
        await core.invoke("clear_startup_error");
      } catch {
        /* ignore */
      }
    }
    // Bump attempt counter so the bootstrap effect re-runs.
    setBootAttempt((n) => n + 1);
    // Fall back to the polling helper in case Rust's idempotent
    // start_backend returns the stale secret of a half-dead supervisor.
    void refreshSecret();
  }, [refreshSecret]);

  const handleBootOpenLog = useCallback(async () => {
    const core = await import("@tauri-apps/api/core").catch(() => null);
    if (!core) return;
    try {
      await core.invoke("open_log_dir");
    } catch (e) {
      console.warn("[bootstrap] open_log_dir failed:", e);
    }
  }, []);

  const handleBootExit = useCallback(async () => {
    // Try the process-plugin exit helper first; if unavailable, close
    // the current window which triggers our WindowEvent::Destroyed path.
    const api = await import("@tauri-apps/api/window").catch(() => null);
    if (api?.getCurrentWindow) {
      try {
        await api.getCurrentWindow().close();
        return;
      } catch {
        /* noop */
      }
    }
    window.close();
  }, []);

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
      // Re-surface as a startup error so the user gets the same dialog
      // affordances (retry / open log dir) without having to re-invoke.
      (async () => {
        const core = await import("@tauri-apps/api/core").catch(() => null);
        let msg =
          "Backend supervisor gave up after repeated crashes. 请打开日志目录排查。";
        if (core) {
          try {
            const cached = await core.invoke<string | null>("get_startup_error");
            if (cached) msg = cached;
          } catch {
            /* ignore */
          }
        }
        setBootError(msg);
        setBootState("failed");
      })();
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

  // Audio player — P2-2-M2 起走 PCM16 24kHz 流式播放（jitter buffer →
  // WebAudio 时间轴调度），不再需要等 tts_end 做整段 MP3 解码。
  const {
    isPlaying,
    stop: stopPlayback,
    reset: resetPlaybackBuffer,
    primeContext,
    bargeIn,
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
          // 前端 VAD 在后端 BargeInFilter 之前先触发：立刻淡出在播音频
          // + 清 jitter buffer，避免给后端 TTS 打断事件到达前还在灌声。
          if (isPlaying) {
            bargeIn();
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
        // 语音链路只经由 audio 通道，不走 control 通道的 chat_response ——
        // 这里复用 assistant transcript 上捎带的 provider 字段来刷新路由
        // 指示灯的颜色（green=local / blue=cloud），否则纯语音用户会一直
        // 停在灰色 "connected"。
        if (
          audioMessage.payload.role === "assistant" &&
          audioMessage.payload.provider
        ) {
          setRouteKind(audioMessage.payload.provider);
        }
        break;

      case "tts_end":
        // PCM 流式模式下每块已实时播放，tts_end 只是终态信号：关嘴 +
        // 回到 listening。jitter buffer 的 startedRef 由 hook 内自行复位。
        setMouthOpenY(0);
        setVadStatus("listening");
        break;

      case "tts_barge_in":
        // P2-2: backend VAD detected user speech during TTS — stop playback.
        console.log("[App] TTS barge-in — stopping playback");
        bargeIn();
        setMouthOpenY(0);
        break;
    }
  }, [audioMessage, isPlaying, resetPlaybackBuffer, bargeIn]);

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
                    // 已连接但还不知道路由到本地/云端（首条消息前，或纯语音
                    // 交互 transcript 里暂缺 provider 字段）—— 按"其他情况"
                    // 的灰色处理，等首条 chat_response / 带 provider 的 transcript
                    // 到来再切到绿色(local) / 蓝色(cloud)。
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

      {/* P3-S8 — splash / error overlay. Renders above everything while
          the backend is still starting or has failed to start. */}
      <StartupOverlay
        state={bootState}
        errorMessage={bootError}
        onRetry={handleBootRetry}
        onOpenLogDir={handleBootOpenLog}
        onExit={handleBootExit}
      />

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
