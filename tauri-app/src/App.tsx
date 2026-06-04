// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import { Live2DCanvas, type Live2DHandle } from "./components/Live2DCanvas";
// Pet Animation UX v2 — B1/B2/B3/D1/C1/C2 observer modules.
import {
  createUserInputObserver,
  type UserInputState,
} from "./pet-anim/userInputObserver";
import { createThinkingObserver } from "./pet-anim/thinkingObserver";
import { createPhonemeEstimator } from "./pet-anim/phonemeEstimator";
import { classifyEmotionVoting } from "./pet-anim/emotionClassifier";
import { isEmotionCode } from "./pet-anim/emotionMapper";
import {
  createIdleWatcher,
  type IdleWatcherState,
  type WelcomeIntensity,
} from "./pet-anim/idleWatcher";
import { V2_CLIENT_VERSION } from "./ws/ControlChannel";
void V2_CLIENT_VERSION; // silence unused import (re-exported for diagnostics later)
import { createTimeCelebration } from "./pet-anim/timeCelebration";
import {
  createMilestoneClient,
  type MilestoneEvent,
  type MilestoneClientState,
} from "./pet-anim/milestoneClient";
import { pickEdge, type Edge } from "./pet-anim/edgeWatcher";
import {
  createOcclusionWatcher,
  type OcclusionState,
  type TopWindowInfo,
} from "./pet-anim/occlusionWatcher";
import {
  createDNDDetector,
  type DNDState,
} from "./pet-anim/dndDetector";
import { PetCelebrationBubble } from "./pet-anim/PetCelebrationBubble";
import { PetDNDBadge } from "./pet-anim/PetDNDBadge";
import { MemoryPanel } from "./components/MemoryPanel";
import { ContextBreakdownModal } from "./components/ContextBreakdownModal";
import { ContextTracePanel } from "./components/ContextTracePanel";
import { SettingsPanel } from "./components/SettingsPanel";
import { DialogBar } from "./components/DialogBar";
import { UserBubble } from "./components/UserBubble";
import { StartupOverlay, type BootState } from "./components/StartupOverlay";
import { useBudgetToast } from "./hooks/useBudgetToast";
import { invoke } from "@tauri-apps/api/core";
import { useControlChannel } from "./hooks/useWebSocket";
import { usePermissionRequests } from "./hooks/usePermissionRequests";
import { PermissionPopup } from "./components/PermissionPopup";
import { SkillStorePanel } from "./components/SkillStorePanel";
import { Toolbar } from "./components/Toolbar";
import { Icon } from "./components/Icon";
// WI-01/02 (beta-100): first-run onboarding wizard + in-app feedback.
import { OnboardingWizard } from "./components/OnboardingWizard";
import { FeedbackPanel } from "./components/FeedbackPanel";
import { onboardingStatus, onboardingComplete } from "./bindings/onboarding";
import { buildDiagnosticBundle } from "./bindings/diagnostics";
import { updateCloudConfig } from "./bindings/config";
import { CodeModePanel } from "./components/CodeModePanel";
import { PetSupervisorBubble } from "./components/PetSupervisorBubble";
import { PetDebugOverlay } from "./components/PetDebugOverlay";
import { useAudioChannel } from "./hooks/useAudioChannel";
import { useAudioRecorder } from "./hooks/useAudioRecorder";
import { useAudioPlayer } from "./hooks/useAudioPlayer";
import { useUpdateChecker } from "./hooks/useUpdateChecker";
import { useAutostart } from "./hooks/useAutostart";
import { useBackendLifecycle } from "./hooks/useBackendLifecycle";
import { useSessionsStore } from "./stores/sessionsStore";
import { PetStateMachine } from "./pet-state/PetStateMachine";
import type { AudioMessage, LipSyncMessage } from "./types/messages";
import { BACKEND_PORT } from "./backendPort";
// W3.3 (relay integration): lazy-mount the relay edition UI only when
// the active adapter is RelayAuthAdapter. OSS default (`manual` /
// `null` editions) never instantiates this component, so its presence
// here is a zero-cost import at build time and a no-op at runtime.
import { getAuthAdapter } from "./auth";
import { RelayAuthAdapter } from "./auth/RelayAuthAdapter";
import { RelayEdition } from "./auth/RelayEdition";
import { relayProviderBridge } from "./auth/relayProviderBridge";
import { friendlyChatErrorMessage } from "./auth/relayErrorText";

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
    // P4-S18 dev hatch: `?secret=xxx` URL param lets us run the SPA in
    // a plain browser (Tauri runtime absent → no get_shared_secret).
    // No effect inside the real Tauri shell because the URL there has
    // no query string. Only honoured in dev (import.meta.env.DEV).
    if (import.meta.env.DEV) {
      const urlSecret = new URLSearchParams(window.location.search).get("secret");
      if (urlSecret) {
        setSecret(urlSecret);
        return;
      }
    }
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
    // P4-S21 #7: prefer the dedicated `app_exit` Rust command so the
    // backend supervisor gets a clean shutdown (no orphan deskpet-backend.exe
    // hanging onto port 8100). Falls through to window.close on older
    // builds that don't have the command registered yet.
    const core = await import("@tauri-apps/api/core").catch(() => null);
    if (core?.invoke) {
      try {
        await core.invoke("app_exit");
        return;
      } catch {
        // Command might not be registered (older Rust binary). Fall
        // through to window.close which triggers WindowEvent::Destroyed
        // and the same kill_child path as a backup.
      }
    }
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

  // ─── Pet Animation UX v2 observer refs (B1/B2). Singletons per App mount. ───
  // B1: user-input observer wraps focus/blur/keydown/composition events on
  // chat-input. Trailing 1.5s idle timer is driven by an animation-frame tick.
  const userInputObsRef = useRef(
    createUserInputObserver({ stop_after_idle_ms: 1500, ime_aware: true }, (active, t) => {
      liveRef.current?.setUserInputActive(active, t);
    }),
  );
  const userInputStateRef = useRef<UserInputState>(userInputObsRef.current.init());

  // B2: thinking observer fires on sendChatV2 and exits on first chunk / final / error.
  const thinkingObsRef = useRef(
    createThinkingObserver({ max_duration_ms: 90_000 }, (active, t) => {
      liveRef.current?.setThinkingActive(active, t);
    }),
  );

  // B4 helper: track last lip_sync timestamp so we can arm the 800ms fallback.
  const lastLipSyncTsRef = useRef<number>(-Infinity);

  // B3 phoneme estimator (fallback path — runs when backend lacks 'viseme' feature).
  const phonemeEstimatorRef = useRef(createPhonemeEstimator({ ms_per_char: 200 }));

  // C1/C2 idle watcher. onLowEnergy → setLowEnergy(true); onWakeup → triggerWelcome.
  const idleCallbacksRef = useRef<{
    onLowEnergy: (now_t: number) => void;
    onWakeup: (now_t: number, dur: number, intensity: WelcomeIntensity) => void;
  }>({
    onLowEnergy: (now_t) => {
      liveRef.current?.setLowEnergy(true, now_t);
    },
    onWakeup: (now_t, _dur, intensity) => {
      liveRef.current?.setLowEnergy(false, now_t);
      liveRef.current?.triggerWelcome(intensity, now_t);
    },
  });
  const idleWatcherRef = useRef(
    createIdleWatcher(
      {
        low_energy_threshold_ms: 300_000,
        welcome_cooldown_ms: 60_000,
        welcome_bubble_threshold_ms: 900_000,
        welcome_intense_threshold_ms: 3_600_000,
      },
      {
        onLowEnergy: (t) => idleCallbacksRef.current.onLowEnergy(t),
        onWakeup: (t, d, i) => idleCallbacksRef.current.onWakeup(t, d, i),
      },
    ),
  );
  const idleStateRef = useRef<IdleWatcherState>(idleWatcherRef.current.init());

  // ─── S3 modules ───
  // C3 time celebration + bubble.
  const [celebrationBubble, setCelebrationBubble] = useState<{
    visible: boolean;
    message: string;
  }>({ visible: false, message: "" });
  // F1 DND active mirror (state so badge re-renders).
  const [dndActiveUI, setDndActiveUI] = useState(false);
  const timeCelebrationRef = useRef(
    createTimeCelebration(
      {
        hourly_enabled: true,
        anniversaries: [],
        dnd_check: () => liveRef.current?.getV2Debug().dnd_active ?? false,
      },
      (kind, message, now_t) => {
        // anniversary suppresses DND check inside the module
        liveRef.current?.triggerCelebration(kind, message, now_t);
        setCelebrationBubble({ visible: true, message });
      },
    ),
  );

  // D2 milestone client (consumes backend pet_milestone ws → FIFO queue).
  const milestoneClientRef = useRef(
    createMilestoneClient(
      { celebration_ms: 3000 },
      {
        onCelebrationStart: (ev, now_t) => {
          liveRef.current?.triggerCelebration("milestone", ev.message, now_t);
          setCelebrationBubble({ visible: true, message: ev.message });
        },
        onCelebrationEnd: () => {
          setCelebrationBubble({ visible: false, message: "" });
        },
      },
    ),
  );
  const milestoneStateRef = useRef<MilestoneClientState>(
    milestoneClientRef.current.init(),
  );

  // E2 occlusion watcher (1Hz poll; degrades to 0.2Hz if Rust API absent).
  const occlusionWatcherRef = useRef(
    createOcclusionWatcher(
      { threshold_ratio: 0.5, grace_ms: 5000 },
      {
        fetchTopWindows: async (): Promise<TopWindowInfo[]> => {
          try {
            // Rust command not yet wired (D0-07 FAIL → fallback).
            const r = await invoke<TopWindowInfo[]>("enumerate_top_windows");
            return Array.isArray(r) ? r : [];
          } catch {
            // Silent disable per PRD §3 E2 graceful degrade.
            throw new Error("enumerate_top_windows unavailable");
          }
        },
        onOccluded: (spot, now_t) => {
          if (spot) {
            void invoke("set_window_position", { x: spot.x, y: spot.y }).catch(
              () => {
                /* silent — Rust command absent */
              },
            );
            liveRef.current?.setEdgeAttached(null, now_t); // ensure edge isn't stuck
          }
        },
        onClear: () => {
          /* no-op for now */
        },
      },
    ),
  );
  const occlusionStateRef = useRef<OcclusionState>(
    occlusionWatcherRef.current.init(),
  );

  // F1 DND detector (composes fullscreen + typing + audio session).
  const dndDetectorRef = useRef(
    createDNDDetector(
      {
        typing_kpm_threshold: 250,
        typing_window_ms: 180_000,
      },
      {
        fetchFullscreen: async () => {
          try {
            return Boolean(await invoke<boolean>("is_foreground_fullscreen"));
          } catch {
            throw new Error("fullscreen API unavailable");
          }
        },
        fetchCallActive: async () => {
          try {
            return Boolean(await invoke<boolean>("is_any_audio_capture_active"));
          } catch {
            throw new Error("audio session API unavailable");
          }
        },
        onChange: (active, reasons, now_t) => {
          liveRef.current?.setDNDActive(active, reasons, now_t);
          setDndActiveUI(active);
        },
      },
    ),
  );
  const dndStateRef = useRef<DNDState>(dndDetectorRef.current.init());

  // P5-S3 — pet supervisor state machine. Single instance per App;
  // recomputed on every relevant store update. Refs (not state) to
  // avoid feedback loops with the render-driven tick.
  const petSmRef = useRef<PetStateMachine>(new PetStateMachine());
  const [petTick, setPetTick] = useState(0);  // simple "force re-tick" counter
  const sessions = useSessionsStore((s) => s.sessions);
  const applySupervisorAlert = useSessionsStore((s) => s.apply_supervisor_alert);
  const clearSupervisorAlert = useSessionsStore((s) => s.clear_supervisor_alert);
  const ensureSession = useSessionsStore((s) => s.ensure);
  // 2026-05-17 桌宠窗左侧常驻消息面板 —— 复用 MessageStreamPanel。
  // 消息面板是**独立窗口**。点 ▶消息 = Rust toggle_message_panel
  // （显↔隐，权威返回新可见态）。`leftPanelOpen` 由 Rust 发的
  // `message-panel-visibility` 事件驱动（同时覆盖「面板自己的 ◀」），
  // 用来在面板打开时隐藏桌宠底部 DialogBar（#1/#2）。面板首次打开
  // 吸附在桌宠左侧；之后可自由拖动/缩放/全屏，桌宠移动不再强拽它
  // 回来（#4：用户的手动摆放优先于自动吸附）。
  const [leftPanelOpen, setLeftPanelOpen] = useState(false);
  const togglePanel = useCallback((next: boolean) => {
    // next is advisory; Rust is authoritative (it checks is_visible).
    // The visibility event below syncs leftPanelOpen either way.
    void next;
    invoke("toggle_message_panel").catch((e: unknown) =>
      console.warn("[Pet] message-panel toggle failed:", e),
    );
  }, []);

  // Sync leftPanelOpen from the Rust visibility event — fires for the
  // pet's ▶消息 toggle AND the panel window's own ◀ collapse, so the
  // DialogBar hide/show is always correct.
  useEffect(() => {
    let cancelled = false;
    let unlisten: (() => void) | undefined;
    void (async () => {
      try {
        const ev = await import("@tauri-apps/api/event");
        const off = await ev.listen<boolean>(
          "message-panel-visibility",
          (e) => setLeftPanelOpen(!!e.payload),
        );
        if (cancelled) off();
        else unlisten = off;
      } catch (e) {
        console.warn("[Pet] visibility listen failed:", e);
      }
    })();
    return () => {
      cancelled = true;
      if (unlisten) unlisten();
    };
  }, []);
  // Recompute pet state on session change OR every 5s so age_penalty
  // grows even without new events.
  useEffect(() => {
    const id = window.setInterval(() => setPetTick((n) => n + 1), 5000);
    return () => window.clearInterval(id);
  }, []);

  // 2026-05-31: 删除前端 resize → invoke("set_window_geometry") 兜底循环。
  // 该 useEffect 是"拉伸后自动缩小两次"的根因 —— set_window_geometry 命令
  // 内部调用 win.set_size(LogicalSize)，会再触发一次 WindowEvent::Resized，
  // 而 window.innerWidth (CSS inner) 与 set_size (outer) 在 DPI/边框场景
  // 下相差几像素，每轮收缩一些 → 用户感知"缩小两次"后稳定。
  // 持久化已由 Rust 端 ResizeDebouncer（lib.rs WindowEvent::Resized 钩子）
  // 独家负责，不需要前端冗余写盘。

  // Control channel (text chat + interrupt + emotion/action events)
  const { state, lastMessage, sendChatV2, sendInterrupt, getChannel: getControlChannel } =
    useControlChannel(BACKEND_PORT, secret);

  // 2026-05-18: 连接(或重连)后从 SessionDB 回灌 default 会话历史，
  // 使左侧消息面板重启后也显示历史记录（后端 session_messages_load
  // → session_messages_response，已在上面 lastMessage switch 处理）。
  const historyLoadedRef = useRef(false);
  useEffect(() => {
    if (state !== "connected") {
      historyLoadedRef.current = false;
      return;
    }
    if (historyLoadedRef.current) return;
    const ch = getControlChannel();
    if (!ch) return;
    try {
      ch.send({
        type: "session_messages_load",
        payload: { session_id: "default", limit: 200 },
      });
      // 2026-05-31 restore — pull cached context-usage on connect so the
      // ring gauge in toolbar hydrates immediately.
      try {
        ch.send({
          type: "context_usage_request",
          payload: { session_id: "default" },
        });
      } catch { /* best-effort */ }
      historyLoadedRef.current = true;
    } catch (e) {
      console.warn("[Pet] session_messages_load send failed:", e);
    }
  }, [state, getControlChannel]);

  // P4-S20: toggle to route chat through the new tool_use loop
  // P4-S20-LLM-Unified: chat 路径已统一 — backend `chat` 和 `chat_v2`
  // msg_type 都走 tool_use AgentLoop。
  // P4-S21 #14: 删掉 useToolUseLoop 状态 + Toolbar toggle。前端永远走
  // chat_v2 路径（即 sendChatV2）。

  // Reset route kind when disconnected.
  useEffect(() => {
    if (state !== "connected") setRouteKind(null);
  }, [state]);

  // S14 — memory management panel toggle.
  const [memoryOpen, setMemoryOpen] = useState(false);
  // 2026-05-31 restore — context-usage breakdown modal (ring drill-down).
  const [contextModalOpen, setContextModalOpen] = useState(false);
  // P4-S11 §16.5 — ContextTrace panel (decision timeline + token budget)
  const [traceOpen, setTraceOpen] = useState(false);

  // P4-S22 — Code mode state + todos. Single source of truth at App
  // level; CodeModePanel renders the banner/todo UI from these props.
  const [codeModeState, setCodeModeState] = useState<{
    enabled: boolean;
    project_root?: string;
    project_name?: string;
  }>({ enabled: false });
  const [codeTodos, setCodeTodos] = useState<
    { content: string; activeForm: string; status: "pending" | "in_progress" | "completed" }[]
  >([]);
  const [codeSuggest, setCodeSuggest] = useState<{ trigger_text: string } | null>(null);
  const codeEnterHandlerRef = useRef<(() => Promise<void>) | null>(null);

  // Subscribe to control-WS messages relevant to code mode.
  useEffect(() => {
    if (!lastMessage) return;
    if (lastMessage.type === "code_mode_state") {
      const p: any = lastMessage.payload || {};
      setCodeModeState({
        enabled: !!p.enabled,
        project_root: p.project_root,
        project_name: p.project_name,
      });
      // P4-S23: Auto-open the secondary code-panel window on enter,
      // close it on exit. The panel takes ownership of the chat
      // surface from here on; the pet's DialogBar shows a placeholder
      // (see latestAssistant render below) so the user isn't seeing
      // the same chat in two places.
      const core = import("@tauri-apps/api/core");
      if (p.enabled) {
        // ask backend for current todos to render.
        const ch = getControlChannel();
        if (ch) ch.send({ type: "code_todo_list" });
        core.then(({ invoke }) => {
          invoke("open_code_panel").catch((e: unknown) =>
            console.warn("[App] open_code_panel failed:", e),
          );
        });
      } else {
        setCodeTodos([]);
        core.then(({ invoke }) => {
          invoke("close_code_panel").catch(() => { /* no-op if missing */ });
        });
      }
    } else if (lastMessage.type === "code_todo_update") {
      const items = (lastMessage.payload as any)?.items || [];
      setCodeTodos(items);
    } else if (lastMessage.type === "code_mode_suggest") {
      const p: any = lastMessage.payload || {};
      setCodeSuggest({ trigger_text: p.trigger_text || "" });
    }
  }, [lastMessage]);

  // P2-1-S3 — settings panel toggle (cloud account / strategy / daily budget).
  const [settingsOpen, setSettingsOpen] = useState(false);

  // WI-01 (beta-100): first-run onboarding. `onboardingNeeded` flips
  // true only when Rust reports no completion marker. Conservative on
  // error (stay false) so a path-resolution hiccup never traps the
  // user in a wizard.
  const [onboardingNeeded, setOnboardingNeeded] = useState(false);
  useEffect(() => {
    let cancelled = false;
    onboardingStatus()
      .then((s) => {
        if (!cancelled) setOnboardingNeeded(s.status === "needs_onboarding");
      })
      .catch(() => {
        /* conservative: never show the wizard if status can't be read */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // WI-02 (beta-100): in-app feedback panel visibility.
  const [feedbackOpen, setFeedbackOpen] = useState(false);

  // 2026-05-19 — pet-window error banner. chat_v2_error used to be
  // pushed as a "⚠ ..." assistant bubble, which then dominated the
  // bottom DialogBar and visually blocked the pet until the next
  // message. Now errors surface in a dedicated dismissible banner
  // pinned to the TOP of the pet column, ABOVE the toolbar — never
  // over the character. User-dismissed only (no auto-clear: an error
  // shouldn't silently vanish before the user notices it).
  const [petError, setPetError] = useState<string | null>(null);

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

  // P4-S20 Wave 1c — permission popup IPC wiring. Runs only when the
  // control channel is open; backend sends `permission_request`, hook
  // queues them and shows one at a time. ESC denies.
  const permissionChannel =
    state === "connected" ? getControlChannel() : null;
  const { current: permissionCurrent, resolve: resolvePermission } =
    usePermissionRequests(permissionChannel);

  // P4-S20 Stage C — skill store panel toggle
  const [skillStoreOpen, setSkillStoreOpen] = useState(false);

  // VN 底栏 —— 最新用户输入（驱动 UserBubble 淡出计时）+ 历史面板开关。
  const [latestUserInput, setLatestUserInput] = useState<string | null>(null);

  // Audio channel (voice pipeline)
  const {
    state: audioState,
    lastMessage: audioMessage,
    sendAudio,
    getChannel,
  } = useAudioChannel(BACKEND_PORT, secret);

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
    const t = (lastMessage as { type?: string }).type;
    switch (t) {
      case "chat_response":
        setMessages((prev) => [
          ...prev,
          { role: "assistant", text: (lastMessage as any).payload.text },
        ]);
        // Update route indicator based on which provider actually served.
        if ((lastMessage as any).payload.provider) {
          setRouteKind((lastMessage as any).payload.provider);
        }
        break;
      case "emotion_change":
        // Push named expression to Live2D. Unknown names silently no-op.
        liveRef.current?.setExpression((lastMessage as any).payload.value);
        break;
      case "action_trigger":
        // Trigger named motion group. Unknown names silently no-op.
        liveRef.current?.playMotion((lastMessage as any).payload.value);
        break;
      // P4-S20 chat_v2 stream events
      case "tool_use_event": {
        // v2 B2 M-1: first stream chunk → exit thinking immediately.
        thinkingObsRef.current.notifyFirstChunk(performance.now());
        const payload = (lastMessage as any).payload || {};
        const kind = payload.kind || "";
        const tool = payload.tool_name || "";
        if (kind === "request") {
          setMessages((prev) => [
            ...prev,
            {
              role: "assistant",
              text: `🔧 调用 ${tool}(${JSON.stringify(payload.params || {})})`,
            },
          ]);
        } else if (kind === "result") {
          const r = payload.result;
          const ok = (r && typeof r === "object" && (r as any).ok === false) ? "❌" : "✅";
          setMessages((prev) => [
            ...prev,
            { role: "assistant", text: `${ok} ${tool} 结果` },
          ]);
        }
        break;
      }
      case "context_usage": {
        // 2026-05-31 restore — context-usage ring update from backend.
        const cu = (lastMessage as any).payload;
        if (cu?.session_id) {
          useSessionsStore.getState().ensure(cu.session_id);
          useSessionsStore.getState().upsert(cu.session_id, { context_usage: cu });
        }
        break;
      }
      case "chat_v2_user_echo": {
        // 2026-05-31 restore — multi-window sync: peer typed a user message.
        // Backend skips originator so receiving means peer-origin → push.
        const echoText = (lastMessage as any).payload?.text;
        if (typeof echoText === "string" && echoText.length > 0) {
          setMessages((prev) => [...prev, { role: "user", text: echoText }]);
        }
        break;
      }
      case "chat_v2_final": {
        // v2 B2: defensive close in case first_chunk path was missed.
        thinkingObsRef.current.notifyEnd(performance.now());
        const finalPayload = (lastMessage as any).payload || {};
        const finalText = finalPayload.text || "(完成)";
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            text: finalText,
          },
        ]);
        // v2 D1: emotion — prefer backend-provided field, else fall back to voting classifier.
        const now = performance.now();
        const backendEmotion = finalPayload.emotion;
        if (isEmotionCode(backendEmotion)) {
          liveRef.current?.setEmotion(backendEmotion, now);
        } else if (typeof finalText === "string" && finalText.trim().length > 0) {
          const classified = classifyEmotionVoting(finalText);
          liveRef.current?.setEmotion(classified, now);
        }
        // v2 B3 fallback: if the backend doesn't advertise viseme support, generate
        // phoneme-estimated viseme frames so the lipsync still moves. Main-path
        // 'tts_viseme' messages (when backend has the feature) handled below.
        const channel = getControlChannel();
        const visemeSupported = !!channel?.hasServerFeature("viseme");
        if (!visemeSupported && typeof finalText === "string" && finalText.length > 0) {
          const dur = Math.max(400, finalText.length * 200);
          const frames = phonemeEstimatorRef.current.estimate(finalText, dur, now);
          liveRef.current?.setPhonemeEstimatorReady(frames, now);
        }
        break;
      }
      case "session_messages_response": {
        // 2026-05-18: 重启/连接后从 SessionDB 回灌历史会话 → 左侧消息
        // 面板显示历史记录（之前只有实时消息，重启即空）。只取
        // user/assistant（tool 行非对话，streamChat 的 forPet 也会滤）；
        // 这是权威近 200 条快照，直接替换 messages。
        const p: any = (lastMessage as any).payload || {};
        const rows: any[] = Array.isArray(p.messages) ? p.messages : [];
        const hist = rows
          .filter((r) => r && (r.role === "user" || r.role === "assistant"))
          .map((r) => ({
            role: r.role as "user" | "assistant",
            text: String(r.text ?? ""),
          }));
        setMessages(hist);
        break;
      }
      case "chat_v2_error": {
        // v2 B2: error closes thinking state.
        thinkingObsRef.current.notifyEnd(performance.now());
        // P4-S22 fix: render whatever the backend sent — `error`
        // (catch-all path), `detail` (AgentLoop ErrorEvent), or
        // `reason`. WI-R5: a relay `error_class` (insufficient_balance /
        // relay_key_invalid) is translated into a friendly Chinese
        // message via friendlyChatErrorMessage instead of a raw HTTP
        // error string.
        const p: any = (lastMessage as any).payload || {};
        const msg = friendlyChatErrorMessage(p);
        // Surface in the top error banner instead of injecting a
        // "⚠ ..." assistant bubble that the bottom DialogBar would
        // then render over the pet persistently.
        setPetError(msg);
        // WI-R5: key 失效 → drive the cross-layer recovery loop so the
        // next message works (fetch a fresh key → re-push to backend).
        if (p.error_class === "relay_key_invalid" && relayAdapter) {
          void relayProviderBridge.recoverFromKeyInvalid(relayAdapter);
        }
        break;
      }
      case "supervisor_alert": {
        // P5-S2/S3 — supervisor pushed a diagnosis. Cache it on the
        // session so PetStateMachine + bubble can read it. Also force
        // a tick so the visual updates immediately (without waiting
        // for the 5s polling interval).
        const p: any = (lastMessage as any).payload || {};
        if (p.session_id) {
          // Make sure the session exists in the store (supervisor may
          // alert on a panel-only sid the pet window hasn't seen yet).
          ensureSession(p.session_id);
          applySupervisorAlert(p.session_id, {
            alert_id: String(p.alert_id || ""),
            severity: (p.severity as "green" | "yellow" | "red") || "yellow",
            action: (p.action as "nudge" | "ask_user") || "nudge",
            diagnosis: String(p.diagnosis || ""),
            user_message: String(p.user_message || ""),
            suggested_buttons: Array.isArray(p.suggested_buttons)
              ? p.suggested_buttons.map((b: any) => String(b)).slice(0, 2)
              : [],
            received_at: Date.now(),
          });
          // Force a re-tick so the bubble appears + state machine
          // transitions on the next render.
          setPetTick((n) => n + 1);
        }
        break;
      }
    }
  }, [lastMessage, applySupervisorAlert, ensureSession]);

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
        // v2 B4 — replace the v1 instant snap with a 200ms ease-out fade.
        // Bypasses setMouthOpenY because the overlay's B4 takes over MouthOpenY
        // for the fade duration (PRD §3 B4).
        liveRef.current?.fadeMouthToZero(200, performance.now());
        // v2 B3: flush any remaining viseme frames now that TTS is over.
        liveRef.current?.flushVisemeQueue();
        // Still set mouthOpenY to 0 so any non-v2 fallback path also returns
        // to a clean state. The overlay precedence keeps the fade smooth.
        setMouthOpenY(0);
        setVadStatus("listening");
        break;

      case "tts_viseme" as string: {
        // v2 B3 main path — backend pushed a viseme frame. We use the
        // payload's t_ms directly (backend may emit absolute timestamps or
        // ms-since-tts-start; either works because visemeLipsync sorts and
        // blends on any monotonic series).
        const vp: any = (lastMessage as any).payload || {};
        if (typeof vp.v === "string" && Number.isFinite(vp.t_ms)) {
          liveRef.current?.setVisemeFrame({ v: vp.v, t_ms: vp.t_ms });
        }
        break;
      }

      case "pet_milestone" as string: {
        // v2 D2 — backend pushed a milestone event. Enqueue via client
        // (FIFO so same-tick milestones don't fire concurrent celebrations).
        const mp: any = (lastMessage as any).payload || {};
        const ev: MilestoneEvent = {
          kind: mp.kind,
          message: typeof mp.message === "string" ? mp.message : "成就达成！",
          achieved_at: Number.isFinite(mp.achieved_at)
            ? mp.achieved_at
            : Date.now(),
        };
        if (
          ev.kind === "streak_7d" ||
          ev.kind === "streak_30d" ||
          ev.kind === "msgs_1000" ||
          ev.kind === "first_custom_prompt" ||
          ev.kind === "first_pet_naming"
        ) {
          milestoneStateRef.current = milestoneClientRef.current.enqueue(
            milestoneStateRef.current,
            ev,
          );
        }
        break;
      }

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
        // v2 B4 — every new lip_sync cancels any pending fade and arms a
        // fresh 800ms silence-timeout (M-4): if the next 800ms passes
        // without another chunk and without tts_end, the fader auto-fades.
        const now = performance.now();
        liveRef.current?.cancelMouthFade();
        liveRef.current?.armMouthFadeTimeout(800, now);
        lastLipSyncTsRef.current = now;
        setMouthOpenY(lipMsg.payload.amplitude);
      }
    });
    return unsub;
  }, [getChannel]);

  // ─── v2 ManualTest §0.2 DevTools helpers (round-1 observability fix). ───
  // Expose injection helpers so manual tests can drive scenarios that don't
  // have natural triggers in dev (e.g. force low_energy without waiting 5min,
  // mock emotion/milestone/DND without backend round-trip).
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    const w = window as unknown as Record<string, unknown>;
    w["__deskpet_anim_fakeIdle"] = (ms_into_low_energy: number) => {
      // Rewind last_activity_t so the next tick crosses the threshold.
      const now = performance.now();
      idleStateRef.current = {
        ...idleStateRef.current,
        last_activity_t: now - (300_000 + Math.max(0, ms_into_low_energy)),
        low_energy: false,
        low_energy_start_t: -Infinity,
      };
    };
    w["__deskpet_fake_emotion"] = (emotion: string) => {
      const now = performance.now();
      // Cast via isEmotionCode for safety.
      if (isEmotionCode(emotion)) {
        liveRef.current?.setEmotion(emotion, now);
      }
    };
    w["__deskpet_fake_milestone"] = (kind: string, message: string) => {
      const ev = {
        kind: kind as MilestoneEvent["kind"],
        message: message || "成就达成！",
        achieved_at: Date.now(),
      };
      milestoneStateRef.current = milestoneClientRef.current.enqueue(
        milestoneStateRef.current,
        ev,
      );
    };
    w["__deskpet_fake_dnd"] = (active: boolean, reasons: string[] = ["fullscreen"]) => {
      const now = performance.now();
      const validReasons = reasons.filter(
        (r): r is "fullscreen" | "typing" | "call" =>
          r === "fullscreen" || r === "typing" || r === "call",
      );
      liveRef.current?.setDNDActive(active, validReasons, now);
      setDndActiveUI(active);
    };
    w["__deskpet_fake_viseme"] = (v: string, t_ms?: number) => {
      const t = Number.isFinite(t_ms as number) ? (t_ms as number) : performance.now();
      if (v === "A" || v === "I" || v === "U" || v === "E" || v === "O" || v === "silent") {
        liveRef.current?.setVisemeFrame({ v, t_ms: t });
      }
    };
    w["__deskpet_fake_celebration"] = (
      kind: "hourly" | "anniversary" | "milestone",
      message: string,
    ) => {
      const now = performance.now();
      liveRef.current?.triggerCelebration(kind, message, now);
      setCelebrationBubble({ visible: true, message });
    };
    w["__deskpet_test_v2_smoke"] = async () => {
      // 13-FR mini smoke: walk each FR through a minimal positive case so the
      // QA agent can sanity-check wiring end-to-end before per-case detail tests.
      const now = performance.now();
      const log: string[] = [];
      liveRef.current?.setDragState("being_held", now);
      log.push("A1 held=being_held");
      await new Promise((r) => setTimeout(r, 200));
      liveRef.current?.setDragState("idle", performance.now());
      liveRef.current?.setUserInputActive(true, performance.now());
      log.push("B1 user_input=true");
      liveRef.current?.setThinkingActive(true, performance.now());
      log.push("B2 thinking=true");
      liveRef.current?.setVisemeFrame({ v: "A", t_ms: performance.now() });
      log.push("B3 viseme=A");
      liveRef.current?.fadeMouthToZero(200, performance.now());
      log.push("B4 fade(200)");
      liveRef.current?.setLowEnergy(true, performance.now());
      log.push("C1 low_energy=true");
      liveRef.current?.triggerWelcome("normal", performance.now());
      log.push("C2 welcome=normal");
      liveRef.current?.triggerCelebration("hourly", "测试", performance.now());
      log.push("C3 celebration=hourly");
      liveRef.current?.setEmotion("happy", performance.now());
      log.push("D1 emotion=happy");
      milestoneStateRef.current = milestoneClientRef.current.enqueue(
        milestoneStateRef.current,
        { kind: "streak_7d", message: "测试", achieved_at: Date.now() },
      );
      log.push("D2 milestone");
      liveRef.current?.setEdgeAttached("right", performance.now());
      log.push("E1 edge=right");
      liveRef.current?.setDNDActive(true, ["fullscreen"], performance.now());
      setDndActiveUI(true);
      log.push("F1 dnd=fullscreen");
      return log;
    };
    return () => {
      try {
        delete w["__deskpet_anim_fakeIdle"];
        delete w["__deskpet_fake_emotion"];
        delete w["__deskpet_fake_milestone"];
        delete w["__deskpet_fake_dnd"];
        delete w["__deskpet_fake_viseme"];
        delete w["__deskpet_fake_celebration"];
        delete w["__deskpet_test_v2_smoke"];
      } catch {
        /* ignore */
      }
    };
  }, []);

  // ─── v2 B1/B2 observer tick (drives trailing-window timeouts at 100ms). ───
  useEffect(() => {
    const interval = window.setInterval(() => {
      const now = performance.now();
      userInputStateRef.current = userInputObsRef.current.tick(
        userInputStateRef.current,
        now,
      );
      thinkingObsRef.current.tick(now);
      // v2 C1: drive low_energy threshold check.
      idleStateRef.current = idleWatcherRef.current.tick(idleStateRef.current, now);
    }, 100);
    return () => window.clearInterval(interval);
  }, []);

  // ─── S3 background polls — C3 + D2 + E2 + F1 ───
  useEffect(() => {
    // C3 / D2 tick every 1s (cheap; just clock math + queue check).
    const slowInterval = window.setInterval(() => {
      const now = performance.now();
      timeCelebrationRef.current.tick(now);
      milestoneStateRef.current = milestoneClientRef.current.tick(
        milestoneStateRef.current,
        now,
      );
    }, 1000);
    // F1 DND tick every 2s (per PRD §3 F1 fullscreen_check_interval_ms).
    const dndInterval = window.setInterval(async () => {
      const now = performance.now();
      dndStateRef.current = await dndDetectorRef.current.tick(dndStateRef.current, now);
    }, 2000);
    // E2 occlusion tick every 1s. Degrades to 0.2Hz on perf overrun (M-18).
    const occInterval = window.setInterval(async () => {
      const now = performance.now();
      try {
        const { getCurrentWindow, currentMonitor } = await import("@tauri-apps/api/window");
        const w = getCurrentWindow();
        const pos = await w.outerPosition();
        const size = await w.outerSize();
        const monitor = await currentMonitor();
        if (!monitor) return;
        const petRect = {
          x: pos.x,
          y: pos.y,
          w: size.width,
          h: size.height,
        };
        const screen = { width: monitor.size.width, height: monitor.size.height };
        occlusionStateRef.current = await occlusionWatcherRef.current.tick(
          occlusionStateRef.current,
          now,
          petRect,
          screen,
        );
      } catch {
        /* Tauri APIs unavailable (e.g. webview during HMR) — silent. */
      }
    }, 1000);
    return () => {
      window.clearInterval(slowInterval);
      window.clearInterval(dndInterval);
      window.clearInterval(occInterval);
    };
  }, []);

  // ─── F1 typing tracker: wire window keydown to dndDetector ring buffer. ───
  useEffect(() => {
    const onKey = () => {
      const now = performance.now();
      dndStateRef.current = dndDetectorRef.current.notifyKeyEvent(
        dndStateRef.current,
        now,
      );
    };
    window.addEventListener("keydown", onKey, { passive: true });
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // ─── E1 edge detection: after a drag ends, check window position vs screen ───
  // We listen for Tauri move events; if not available, fall back to onPointerUp
  // in Live2DCanvas (already wired to setDragState).
  useEffect(() => {
    let unlisten: (() => void) | null = null;
    let snapTimer: number | null = null;
    let cancelled = false;
    (async () => {
      try {
        const { getCurrentWindow, currentMonitor } = await import("@tauri-apps/api/window");
        const w = getCurrentWindow();
        const off = await w.onMoved(async () => {
          // 2026-06-03 多屏拖动修复：onMoved 在拖动中**持续触发**。原来在这里
          // 立即 pickEdge + setPosition 吸边 → 拖到屏幕边缘(含两屏交界)就被吸住、
          // 跨不过去，且 setPosition 又触发 onMoved → 振荡抖动（用户实测：从三星
          // 拖不回小米，剧烈抖动后弹回三星）。改为**防抖**：拖动停下(onMoved 静止
          // ~250ms = 松手)后才吸一次，拖动期间不干预位置 → 可自由跨屏。
          if (snapTimer !== null) {
            window.clearTimeout(snapTimer);
          }
          snapTimer = window.setTimeout(async () => {
            try {
              const pos = await w.outerPosition();
              const size = await w.outerSize();
              const monitor = await currentMonitor();
              if (!monitor) return;
              // 2026-06-03 多屏坐标修复：outerPosition() 返回**全局**物理坐标
              // （非主屏从 monitor.position 偏移起算，如小米屏 x 从 3840 起），而
              // pickEdge/snapTarget 按「显示器局部 0..size」算边缘。必须先减去显示器
              // 原点转成局部坐标，吸附结果再加回原点 —— 否则在非主屏上 pickEdge 永远
              // 误判「右边缘」（全局 x ≫ 局部宽），snapTarget 把桌宠 setPosition 回主屏
              // （用户实测：拖到小米后剧烈抖动弹回三星）。
              const mx = monitor.position.x;
              const my = monitor.position.y;
              const localRect = {
                x: pos.x - mx,
                y: pos.y - my,
                w: size.width,
                h: size.height,
              };
              const screen = {
                width: monitor.size.width,
                height: monitor.size.height,
              };
              const edge: Edge = pickEdge(localRect, screen, 100);
              const now = performance.now();
              liveRef.current?.setEdgeAttached(edge, now);
              if (edge !== null) {
                // Snap by re-positioning slightly out past edge.
                const { snapTarget } = await import("./pet-anim/edgeWatcher");
                const t = snapTarget(localRect, screen, edge, 10);
                if (t) {
                  // 局部吸附目标加回显示器原点 → 全局坐标；仅当与当前位置有
                  // 意义差异时才 setPosition，防「setPosition→onMoved→再吸」自触发循环。
                  const gx = t.x + mx;
                  const gy = t.y + my;
                  if (Math.abs(gx - pos.x) > 2 || Math.abs(gy - pos.y) > 2) {
                    const { PhysicalPosition } = await import(
                      "@tauri-apps/api/window"
                    );
                    await w.setPosition(new PhysicalPosition(gx, gy));
                  }
                }
              }
            } catch {
              /* silent — Tauri API issue */
            }
          }, 250);
        });
        if (cancelled) {
          off();
        } else {
          unlisten = off;
        }
      } catch {
        /* not running under Tauri (dev/preview) */
      }
    })();
    return () => {
      cancelled = true;
      if (snapTimer !== null) {
        window.clearTimeout(snapTimer);
      }
      unlisten?.();
    };
  }, []);

  // ─── v2 C1/C2 global activity listener (PRD §3 C1 events list). ───
  useEffect(() => {
    const onActivity = () => {
      const now = performance.now();
      idleStateRef.current = idleWatcherRef.current.notifyActivity(
        idleStateRef.current,
        now,
      );
    };
    const events: Array<keyof WindowEventMap> = [
      "keydown",
      "mousemove",
      "wheel",
      "pointermove",
      "focus",
      "blur",
    ];
    for (const ev of events) {
      window.addEventListener(ev, onActivity, { passive: true });
    }
    document.addEventListener("visibilitychange", onActivity, { passive: true });
    // Seed: first mount counts as activity so we don't immediately enter low_energy.
    idleStateRef.current = idleWatcherRef.current.notifyActivity(
      idleStateRef.current,
      performance.now(),
    );
    return () => {
      for (const ev of events) {
        window.removeEventListener(ev, onActivity);
      }
      document.removeEventListener("visibilitychange", onActivity);
    };
  }, []);

  const handleSend = () => {
    if (!chatText.trim()) return;
    setMessages((prev) => [...prev, { role: "user", text: chatText }]);
    // 触发 UserBubble —— 每次用新对象 ref 重置淡出计时，避免相同文本重发时
    // React 因为字符串相等不重置 state（追加零宽空格保证每次 text prop 唯一）。
    setLatestUserInput(chatText + "\u200B".repeat(messages.length));
    // v2 B2: enter thinking state right when the request goes out.
    thinkingObsRef.current.notifyStart(performance.now());
    // P4-S21 #14: backend unified chat / chat_v2 — both route to tool_use
    // AgentLoop. Always send via sendChatV2 (the toolbar toggle is gone).
    sendChatV2(chatText);
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
    // v2 D1 (M-11): user-interrupt releases emotion lock immediately.
    const now = performance.now();
    liveRef.current?.setEmotion("neutral", now);
    liveRef.current?.flushVisemeQueue();
    liveRef.current?.cancelMouthFade();
    thinkingObsRef.current.notifyEnd(now);
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

  // P5-S3 — compute pet state every render. The `petTick` dep forces
  // recomputation on supervisor_alert / 5s heartbeat. We immediately
  // apply the resulting motion config to the Live2D handle (refs, no
  // re-mount cost). Bubble visibility is derived below.
  void petTick; // ensure this drives the memo recompute cycle
  const petResult = petSmRef.current.tick({ sessions });
  useEffect(() => {
    const live = liveRef.current;
    if (!live) return;
    live.setBlinkRate(petResult.motion.blink_hz);
    live.setHeadTilt(petResult.motion.head_tilt);
    live.setIdleSubset(petResult.motion.motion_pool);
    // v3 (PRD §6.5 / FR-5): drive AnimationOverlay's motion tag pool.
    // state_changed=true → force_switch_now so a supervisor severity
    // bump swaps the motion immediately rather than waiting for the
    // round-robin period.
    const tags = petResult.motion.motion_tag_pool ?? [];
    live.setMotionTagPool(
      tags,
      { force_switch_now: petResult.state_changed },
      performance.now(),
    );
    if (petResult.state_changed && petResult.motion.tap_on_entry) {
      live.playMotion("TapBody");
    }
  }, [petResult.state, petResult.state_changed, petResult.motion.blink_hz, petResult.motion.head_tilt]);

  // Resolve the active alert to surface in the bubble. We pin the bubble
  // to the focus session's most recent alert; if none, no bubble.
  const focusSession = petResult.focus_sid ? sessions[petResult.focus_sid] : null;
  const focusAlert = focusSession?.supervisor_alert || null;
  const showBubble =
    focusAlert &&
    (petResult.state === "worried" ||
      petResult.state === "alert" ||
      petResult.state === "intervening");

  // Frontend → backend choice handler. Sends `supervisor_user_choice`
  // ws message and clears the bubble locally so it disappears.
  const handleBubbleChoice = useCallback(
    (idx: number, text: string, alert_id: string, sid: string) => {
      const ch = getControlChannel();
      if (ch) {
        ch.send({
          type: "supervisor_user_choice",
          payload: {
            session_id: sid,
            alert_id,
            button_index: idx,
            button_text: text,
          },
        });
      }
      clearSupervisorAlert(sid);
    },
    [getControlChannel, clearSupervisorAlert],
  );

  // ── 2026-05-17: streamChat / streamWarnings / streamErrors /
  // handlePanelJump / handlePanelChoice derivations used to live here
  // for the in-pet-window message panel. The panel was extracted to a
  // separate window (commit 9ebd5ca), and the derivations stopped
  // being consumed in this file. Removed 2026-05-21 to silence
  // noUnusedLocals — see message-panel/MessagePanelRoot.tsx for the
  // live versions.

  // Bubble background click → open code panel and request focus on this sid.
  const handleBubbleClickBackground = useCallback(
    (sid: string) => {
      const core = import("@tauri-apps/api/core");
      core.then(({ invoke }) => {
        invoke("open_code_panel").catch((e: unknown) =>
          console.warn("[Pet] open_code_panel failed:", e),
        );
      });
      // Cross-window event so the code panel can switch its active sid.
      try {
        const bc = new BroadcastChannel("deskpet-pet-focus");
        bc.postMessage({ type: "pet_focus_session_clicked", session_id: sid });
        bc.close();
      } catch (e) {
        console.warn("[Pet] BroadcastChannel failed:", e);
      }
    },
    [],
  );

  // W3.3 (relay integration): identify the active adapter once. Memoised
  // by the auth/index.ts singleton, so re-renders are free. We only
  // mount the relay UI when the adapter is concrete RelayAuthAdapter —
  // OSS default returns a ManualAuthAdapter and `relayAdapter` is null,
  // so the JSX guard below is dead code in that build.
  const relayAdapter = useMemo(() => {
    const a = getAuthAdapter();
    return a instanceof RelayAuthAdapter ? a : null;
  }, []);

  // 2026-05-26: 账户面板触发器 — RelayEdition mount 时把 setShowAccount
  // 写入 .current；Toolbar 的 onAccount 通过这个 ref 触发。pill 视觉挪
  // 进 Toolbar 后两边解耦，RelayEdition 只管 modal。
  const openAccountRef = useRef<(() => void) | null>(null);

  // WI-R3: in relay edition the forced login modal must come BEFORE the
  // onboarding wizard. Track auth state so the wizard is gated on it.
  // Non-relay editions: no adapter → `relayAuthed` stays true → wizard
  // shows normally (zero behaviour change).
  const [relayAuthed, setRelayAuthed] = useState(
    relayAdapter ? relayAdapter.isAuthenticated() : true,
  );
  useEffect(() => {
    if (!relayAdapter) return;
    setRelayAuthed(relayAdapter.isAuthenticated());
    return relayAdapter.onEvent((e) => {
      if (e.type === "login") setRelayAuthed(true);
      if (e.type === "logout") setRelayAuthed(false);
    });
  }, [relayAdapter]);

  return (
    <div
      style={{
        position: "relative",
        width: "100vw",
        height: "100vh",
        backgroundColor: "transparent",
        overflow: "hidden",
      }}
    >
      {/* W3.3: relay-edition UI lives entirely under this single
          conditional. Manual / null editions render zero relay nodes
          and pay zero runtime cost beyond one instanceof check above. */}
      {relayAdapter && (
        <RelayEdition adapter={relayAdapter} openAccountRef={openAccountRef} />
      )}

      {/* 2026-05-19: 消息面板已抽成**独立窗口**（message-panel），不再
          内嵌于桌宠窗。这里不再渲染 aside —— 桌宠窗保持「仅桌宠、全
          透明、零死区」。面板的开关 = Rust open/close_message_panel。 */}

      {/* 右侧 = 原桌宠壳：透明 + `data-tauri-drag-region`。所有现有
          absolute 覆盖层(DialogBar / 输入条 / 气泡 / 弹窗)相对此壳
          定位，与改造前一致 → 行为零回归。空白透明区拖动 = 移动桌宠。 */}
      <div
        data-tauri-drag-region
        style={{
          // 2026-05-19: 消息面板已是独立窗口，桌宠窗尺寸恒定（见
          // tauri.conf.json，永不 resize）。因此桌宠壳直接**铺满整个
          // 窗口**——不再 right:0+width:282 的「贴右固定宽」（那是旧的
          // resize 抗抖 hack，留下 18px 死区还让工具栏溢出被裁）。所有
          // absolute 覆盖层以本壳为定位上下文，宽度=整窗 → 工具栏/
          // DialogBar 不再裁切，▶ tab 在真正的窗口左缘。
          position: "absolute",
          inset: 0,
          backgroundColor: "transparent",
          overflow: "hidden",
        }}
      >
      {/* 2026-05-19 — 错误提示条：钉在桌宠列最顶端、工具栏之上，整列
          通宽，绝不遮挡桌宠人物。用户手动 ✕ 关闭（不自动消失，避免
          没注意到就没了）。错误同时不再灌进底部 DialogBar。 */}
      {petError && (
        <div
          role="alert"
          onMouseDown={(e) => e.stopPropagation()}
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            zIndex: 40,
            display: "flex",
            alignItems: "flex-start",
            gap: 6,
            padding: "6px 8px",
            maxHeight: 60,
            overflowY: "auto",
            background: "rgba(127, 29, 29, 0.94)",
            color: "#fecaca",
            borderBottom: "1px solid rgba(239, 68, 68, 0.55)",
            backdropFilter: "blur(8px)",
            fontSize: 11,
            lineHeight: 1.4,
          }}
        >
          <span style={{ flexShrink: 0 }}>⚠</span>
          <span
            data-bp-selectable=""
            style={{ flex: 1, wordBreak: "break-word", whiteSpace: "pre-wrap" }}
          >
            {petError}
          </span>
          <button
            type="button"
            onClick={() => setPetError(null)}
            title="关闭"
            aria-label="关闭错误提示"
            style={{
              flexShrink: 0,
              background: "transparent",
              border: "none",
              color: "#fecaca",
              cursor: "pointer",
              fontSize: 13,
              lineHeight: 1,
              padding: "0 2px",
            }}
          >
            ✕
          </button>
        </div>
      )}
      {/* 桌宠左缘常驻「▶ 消息」贴标：打开独立消息面板窗口（幂等——
          已开就是再吸附一次）。面板关闭由它自己的 ◀ 负责，故这里不做
          开/关切换，避免跨窗状态不同步。drag-region 内 button 会吞拖动
          所以 stopPropagation。 */}
      {(
        <button
          type="button"
          onClick={() => togglePanel(true)}
          onMouseDown={(e) => e.stopPropagation()}
          title="显示消息面板"
          aria-label="显示消息面板"
          // 左边缘垂直居中的小贴标 —— 避开顶部工具栏(记忆/设置等)
          // 与底部 DialogBar/输入条，不再遮挡「记忆」按钮。
          style={{
            position: "absolute",
            top: "50%",
            left: 0,
            transform: "translateY(-50%)",
            zIndex: 30,
            display: "flex",
            alignItems: "center",
            gap: 5,
            height: 34,
            padding: "0 11px 0 9px",
            background:
              "linear-gradient(180deg, rgba(33,38,58,0.86) 0%, rgba(20,23,34,0.90) 100%)",
            color: "#c7d2fe",
            borderTop: "1px solid rgba(129,140,248,0.40)",
            borderRight: "1px solid rgba(129,140,248,0.40)",
            borderBottom: "1px solid rgba(129,140,248,0.40)",
            borderLeft: "none",
            borderTopRightRadius: 12,
            borderBottomRightRadius: 12,
            fontSize: 11.5,
            fontWeight: 600,
            letterSpacing: 0.3,
            cursor: "pointer",
            backdropFilter: "blur(16px) saturate(1.4)",
            WebkitBackdropFilter: "blur(16px) saturate(1.4)",
            boxShadow:
              "3px 4px 16px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.07)",
            transition: "padding 160ms ease, background 160ms ease",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.paddingRight = "15px";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.paddingRight = "11px";
          }}
        >
          <Icon name="message" size={13} />
          消息
        </button>
      )}
      {/* 收起控件已回归 panel header 最左（清晰固定边缘）。中缝悬浮
          tab 是糟糕交互（漂在消息内容上、还被裁），已移除。 */}
      <Live2DCanvas
        ref={liveRef}
        modelPath="/assets/live2d/hiyori/Hiyori.model3.json"
        onFpsUpdate={handleFpsUpdate}
        mouthOpenY={mouthOpenY}
        // pet 区恒为 282 CSS px（= 改造前小窗宽度）。面板开/关时窗口
        // 物理尺寸变，但 pet 列宽不变 → Hiyori 渲染与窗口/面板状态
        // 完全解耦，切换不重排、不闪。
        petWidth={282}
      />

      {/* P4-S20 — 权限请求弹窗（最高 zIndex） */}
      <PermissionPopup
        request={permissionCurrent}
        onResolve={resolvePermission}
      />

      {/* P5-S1 D — Debug overlay. Only renders when
          localStorage.deskpet_debug === "1". Cheap to leave mounted. */}
      <PetDebugOverlay
        pet_state={petResult.state}
        focus_sid={petResult.focus_sid}
        focus_score={petResult.focus_score}
      />

      {/* P5-S3 — supervisor bubble. Only shown when state machine says
          we should: worried / alert / intervening AND we have a real
          alert payload to render. */}
      {showBubble && focusAlert && petResult.focus_sid && (
        <PetSupervisorBubble
          severity={
            petResult.state === "alert"
              ? "red"
              : petResult.state === "intervening"
              ? "blue"
              : "yellow"
          }
          message={focusAlert.user_message || focusAlert.diagnosis}
          buttons={focusAlert.suggested_buttons}
          session_id={petResult.focus_sid}
          alert_id={focusAlert.alert_id}
          onClickBackground={handleBubbleClickBackground}
          onChoice={handleBubbleChoice}
        />
      )}

      {/* v2 C3 / D2 — celebration bubble (hourly / anniversary / milestone). */}
      <PetCelebrationBubble
        visible={celebrationBubble.visible}
        message={celebrationBubble.message}
        duration_ms={3000}
        onDismiss={() => setCelebrationBubble({ visible: false, message: "" })}
      />

      {/* v2 F1 — DND ZZZ badge. */}
      <PetDNDBadge visible={dndActiveUI} />


      {/* P4-S22 — Code mode UI (banner + suggest + todos) */}
      <CodeModePanel
        state={codeModeState}
        todos={codeTodos}
        suggest={codeSuggest}
        onDismissSuggest={() => setCodeSuggest(null)}
        onAcceptSuggest={() => {
          setCodeSuggest(null);
          const h = codeEnterHandlerRef.current;
          if (h) void h();
        }}
        onExitCodeMode={() => {
          const ch = getControlChannel();
          if (ch) ch.send({ type: "code_mode_exit" });
        }}
        registerEnterHandler={(fn) => {
          codeEnterHandlerRef.current = fn;
        }}
        getChannel={getControlChannel}
      />

      {/* P4-S20 Stage C — 技能商店 */}
      <SkillStorePanel
        open={skillStoreOpen}
        channel={permissionChannel}
        onClose={() => setSkillStoreOpen(false)}
      />

      {/* #2: 独立消息面板打开时，底部 DialogBar 隐藏（二者职责重叠，
          避免双重显示）；面板关闭时回到桌宠+底栏形态。leftPanelOpen
          由 Rust 可见性事件驱动，面板自己的 ◀ 也会同步。 */}
      {!leftPanelOpen && (
        <DialogBar
          latestAssistant={
            latestAssistant ? stripMarkdown(latestAssistant) : null
          }
        />
      )}

      {/* 用户消息 2s 小气泡 */}
      <UserBubble text={latestUserInput} visibleMs={2000} />

      {/* 独立消息面板打开时，桌宠底部输入条（mic+输入框+发送）一并隐藏
          —— 面板已自带同等输入能力，避免双输入入口并存。 */}
      {!leftPanelOpen && (
      <div
        style={{
          position: "absolute",
          bottom: 8,
          left: 8,
          right: 8,
          display: "flex",
          alignItems: "center",
          gap: 6,
          zIndex: 20,
          background:
            "linear-gradient(180deg, rgba(30,35,52,0.82) 0%, rgba(17,20,30,0.88) 100%)",
          padding: "7px 9px",
          borderRadius: 24,
          border: "1px solid rgba(255,255,255,0.10)",
          boxShadow:
            "0 12px 36px rgba(0,0,0,0.42), inset 0 1px 0 rgba(255,255,255,0.08)",
          backdropFilter: "blur(20px) saturate(1.5)",
          WebkitBackdropFilter: "blur(20px) saturate(1.5)",
        }}
      >
        {/* Mic button — pulses while recording */}
        <button
          data-testid="mic-button"
          onClick={toggleRecording}
          disabled={audioState !== "connected" && state !== "connected"}
          style={{
            width: 36,
            height: 36,
            borderRadius: "50%",
            border: `1px solid ${
              isRecording
                ? "rgba(239,68,68,0.55)"
                : vadStatus === "speaking"
                  ? "rgba(245,158,11,0.55)"
                  : "rgba(255,255,255,0.12)"
            }`,
            background: isRecording
              ? "linear-gradient(180deg, #f87171, #ef4444)"
              : vadStatus === "speaking"
                ? "linear-gradient(180deg, #fbbf24, #f59e0b)"
                : "rgba(255,255,255,0.07)",
            color: isRecording || vadStatus === "speaking" ? "#fff" : "#cbd5e1",
            cursor: "pointer",
            animation: isRecording ? "pulse 1.5s infinite" : "none",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
            boxShadow: isRecording
              ? "0 0 14px rgba(239,68,68,0.5)"
              : "none",
            transition: "background 160ms ease, box-shadow 160ms ease",
          }}
          title={isRecording ? "停止录音" : "按住录音"}
        >
          <Icon name={isRecording ? "stop" : "mic"} size={17} />
        </button>

        {/* Interrupt button — TTS playing */}
        {isPlaying && (
          <button
            data-testid="interrupt-button"
            onClick={handleInterrupt}
            style={{
              width: 36,
              height: 36,
              borderRadius: "50%",
              border: "1px solid rgba(239,68,68,0.55)",
              background: "linear-gradient(180deg, #ef4444, #dc2626)",
              color: "white",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
              boxShadow: "0 0 14px rgba(239,68,68,0.45)",
            }}
            title="打断 (Esc)"
          >
            <Icon name="hand" size={17} />
          </button>
        )}

        <input
          data-testid="chat-input"
          className="bp-chat-input"
          type="text"
          value={chatText}
          onChange={(e) => setChatText(e.target.value)}
          onFocus={(e) => {
            // v2 B1: focus arms the observer but doesn't activate until keystroke.
            userInputStateRef.current = userInputObsRef.current.onFocus(
              userInputStateRef.current,
              e.timeStamp,
            );
          }}
          onBlur={(e) => {
            // v2 B1: blur immediately drops active.
            userInputStateRef.current = userInputObsRef.current.onBlur(
              userInputStateRef.current,
              e.timeStamp,
            );
          }}
          onCompositionStart={(e) => {
            // v2 B1 IME: composition keystrokes are pinyin chars, not real input.
            userInputStateRef.current = userInputObsRef.current.onCompositionStart(
              userInputStateRef.current,
              e.timeStamp,
            );
          }}
          onCompositionEnd={(e) => {
            // v2 B1 IME: commit reawakens the trailing window.
            userInputStateRef.current = userInputObsRef.current.onCompositionEnd(
              userInputStateRef.current,
              e.timeStamp,
            );
          }}
          onKeyDown={(e) => {
            // v2 B1: every keystroke resets the 1.5s trailing window (IME-guarded).
            userInputStateRef.current = userInputObsRef.current.onKeydown(
              userInputStateRef.current,
              e.timeStamp,
            );
            handleKeyDown(e);
          }}
          placeholder={
            state === "connected" ? "和桌宠说点什么…" : "连接中…"
          }
          disabled={state !== "connected"}
          style={{
            flex: 1,
            minWidth: 0,
            height: 36,
            padding: "0 15px",
            borderRadius: 18,
            border: "1px solid rgba(255,255,255,0.10)",
            fontSize: 13,
            background: "rgba(255,255,255,0.06)",
            color: "#e8edf6",
            outline: "none",
            fontFamily: "inherit",
            transition: "border-color 140ms ease, box-shadow 140ms ease, background 140ms ease",
          }}
        />
        {(() => {
          const active = state === "connected" && !!chatText.trim();
          return (
            <button
              data-testid="send-button"
              onClick={handleSend}
              disabled={!active}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 5,
                padding: "0 15px",
                height: 36,
                borderRadius: 18,
                border: `1px solid ${active ? "rgba(96,165,250,0.6)" : "rgba(255,255,255,0.07)"}`,
                background: active
                  ? "linear-gradient(180deg, #4f93ff 0%, #2563eb 100%)"
                  : "rgba(255,255,255,0.05)",
                color: active ? "#fff" : "rgba(148,163,184,0.6)",
                fontSize: 13,
                fontWeight: 600,
                cursor: active ? "pointer" : "not-allowed",
                boxShadow: active ? "0 4px 14px rgba(37,99,235,0.42)" : "none",
                transition: "background 140ms ease, box-shadow 140ms ease, transform 120ms ease",
                flexShrink: 0,
              }}
              onMouseEnter={(e) => {
                if (active) {
                  e.currentTarget.style.background =
                    "linear-gradient(180deg, #60a5fa 0%, #1d4ed8 100%)";
                  e.currentTarget.style.transform = "translateY(-1px)";
                }
              }}
              onMouseLeave={(e) => {
                if (active) {
                  e.currentTarget.style.background =
                    "linear-gradient(180deg, #4f93ff 0%, #2563eb 100%)";
                  e.currentTarget.style.transform = "translateY(0)";
                }
              }}
            >
              <Icon name="send" size={14} />
              发送
            </button>
          );
        })()}
      </div>
      )}

      {/* Toolbar — P4-S20-UI revamp: token-based, grouped, hover/focus states.
          P4-S21 #7: now includes a Quit (⏻) button so users don't need
          Task Manager to close the pet after startup. */}
      <Toolbar
        onMemory={() => setMemoryOpen(true)}
        onTrace={() => setTraceOpen(true)}
        onSettings={() => setSettingsOpen(true)}
        onSkillStore={() => setSkillStoreOpen(true)}
        onFeedback={() => setFeedbackOpen(true)}
        onExit={handleBootExit}
        onCodeMode={() => {
          // P4-S23 UX fix: clicking 🔧 just opens the Code panel
          // window; project selection happens INSIDE the panel via
          // its "+ 新项目" button. Previously we forced a folder
          // picker upfront, which created an "untitled" placeholder
          // session before the user even saw the panel.
          const core = import("@tauri-apps/api/core");
          core.then(({ invoke }) => {
            invoke("open_code_panel").catch((e: unknown) =>
              console.warn("[App] open_code_panel failed:", e),
            );
          });
          // Also flip the Toolbar 🔧 active highlight on. We don't
          // call `code_mode_enter` here — backend stays in companion
          // state until the user picks a project from inside the panel.
          setCodeModeState((s) => ({ ...s, enabled: true, project_name: "" }));
        }}
        codeModeActive={codeModeState.enabled}
        onAccount={
          relayAdapter && relayAuthed
            ? () => openAccountRef.current?.()
            : undefined
        }
        autostartReady={autostart.ready}
        autostartEnabled={autostart.enabled}
        onToggleAutostart={autostart.toggle}
        vadStatus={vadStatus}
        isPlaying={isPlaying}
        isRecording={isRecording}
        fps={fps}
        connectionState={state}
        routeKind={routeKind}
        topOffset={petError ? 66 : undefined}
        contextUsage={sessions["default"]?.context_usage ?? null}
        onContextRingClick={() => setContextModalOpen(true)}
      />

      {/* S14 memory management overlay */}
      <MemoryPanel
        open={memoryOpen}
        onClose={() => setMemoryOpen(false)}
        sessionId="default"
        getChannel={getControlChannel}
      />

      {/* 2026-05-31 restore — Context usage breakdown (ring drill-down) */}
      <ContextBreakdownModal
        open={contextModalOpen}
        onClose={() => setContextModalOpen(false)}
        sessionId="default"
        snapshot={sessions["default"]?.context_usage ?? null}
        send={(m) => {
          const ch = getControlChannel();
          if (ch) {
            try { ch.send(m); } catch (e) { console.warn("[ContextModal] send failed:", e); }
          }
        }}
        onMessage={(fn) => {
          const ch = getControlChannel();
          if (!ch) return () => {};
          return ch.onMessage(fn);
        }}
      />

      {/* P4-S11 ContextTrace overlay */}
      <ContextTracePanel
        open={traceOpen}
        onClose={() => setTraceOpen(false)}
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
        relayAdapter={relayAdapter}
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

      {/* WI-01 (beta-100) — first-run onboarding wizard. Shows only on a
          brand-new install (no completion marker). "Test connection"
          reuses the existing update_cloud_config IPC — a successful
          test also persists the config, so the user isn't asked to
          save separately. */}
      {onboardingNeeded && relayAuthed && (
        <OnboardingWizard
          edition={relayAdapter ? "relay" : "manual"}
          onTestConnection={async (cfg) => {
            try {
              const r = await updateCloudConfig("", {
                base_url: cfg.base_url,
                model: cfg.model,
                api_key: cfg.api_key,
              });
              return { ok: !!r.ok };
            } catch (e) {
              return { ok: false, error: String(e) };
            }
          }}
          onComplete={() => {
            void onboardingComplete("0.6.0-beta").catch(() => {});
            setOnboardingNeeded(false);
          }}
          onSkip={() => {
            void onboardingComplete("0.6.0-beta").catch(() => {});
            setOnboardingNeeded(false);
          }}
        />
      )}

      {/* WI-02 (beta-100) — in-app feedback / diagnostic bundle panel. */}
      {feedbackOpen && (
        <FeedbackPanel
          onBuildBundle={(note) => buildDiagnosticBundle(note)}
          onClose={() => setFeedbackOpen(false)}
        />
      )}

      {/* Pulse animation for recording button */}
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.7; transform: scale(1.1); }
        }
      `}</style>
      </div>
    </div>
  );
}

export default App;
