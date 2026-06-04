// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import {
  AnimationOverlay,
  type CoreModelLike,
  type InteractionKind,
  type MotionTag,
} from "../pet-anim";
import { get_calibrated_motion_pools } from "../pet-state/PetStateMachine";

interface Live2DCanvasProps {
  modelPath: string;
  onFpsUpdate?: (fps: number) => void;
  mouthOpenY?: number; // 0.0 ~ 1.0, drives ParamMouthOpenY for lip sync
  /** 2026-05-17: pet 渲染区逻辑宽（CSS px）。桌宠窗加了左侧常驻消息
   * 面板后，窗口变宽但 Hiyori 必须只居中在右侧 pet 列里 —— 否则会
   * 居中到整窗(含面板)被面板挡住/被裁切。App 传 innerWidth - 面板宽。
   * 不传 → 退回整窗宽(旧行为，零回归)。高度仍取 innerHeight。 */
  petWidth?: number;
}

/**
 * Imperative handle for driving Live2D state from parent components.
 *
 * v3 (2026-05-24) Pet Animation UX additions per PRD §6.1:
 *   - setMotionTagPool(tags, opts, now_t) — drive motion choice by tag
 *   - setGazeTarget / clearGazeTarget — manual gaze override (App rarely
 *     uses; we wire window-level pointermove inside the canvas instead)
 *   - pulseInteraction(kind) — emit synthetic pointer reactions for tests
 *   - getAnimationMetrics / getAnimationDebug — expose perf + state to
 *     ManualTest CASE-MET / CASE-G / CASE-MP via window globals
 */
export interface Live2DHandle {
  /** Apply a named expression. Silently no-ops if unknown/unloaded. */
  setExpression: (name: string) => void;
  /** Trigger a motion group. Silently no-ops if unknown/unloaded. */
  playMotion: (group: string) => void;
  /** P5-S3: eye-blink frequency Hz overlaid on base motion. */
  setBlinkRate: (hz: number) => void;
  /** P5-S3: persistent head tilt (degrees). */
  setHeadTilt: (degrees: number) => void;
  /** P5-S3: advisory hint for Idle motion subset. */
  setIdleSubset: (motionIds: string[]) => void;
  /** v3 PRD FR-5: drive motion by tag pool. */
  setMotionTagPool: (
    tags: MotionTag[],
    opts: { force_switch_now: boolean },
    now_t: number,
  ) => void;
  /** v3 PRD FR-4 (rarely-needed manual override). */
  setGazeTarget: (clientX: number, clientY: number, now_t: number) => void;
  clearGazeTarget: (now_t: number) => void;
  /** v3 PRD FR-6 (test/synthetic). */
  pulseInteraction: (kind: InteractionKind) => void;
  /** v3 PRD FR-7. */
  getAnimationMetrics: () => ReturnType<AnimationOverlay["getAnimationMetrics"]>;
  /** v3 PRD §6.1 debug surface. */
  getAnimationDebug: () => ReturnType<AnimationOverlay["getAnimationDebug"]>;
  /** v2 PRD §6.1: A1 drag state machine input. */
  setDragState: (state: "idle" | "being_held", now_t: number) => void;
  /** v2 PRD §6.1: B1 user-input observer wiring. */
  setUserInputActive: (active: boolean, now_t: number) => void;
  /** v2 PRD §6.1: B2 thinking observer wiring. */
  setThinkingActive: (active: boolean, now_t: number) => void;
  /** v2 PRD §6.1: B4 deterministic mouth fade. */
  fadeMouthToZero: (duration_ms: number, now_t: number) => void;
  /** v2 PRD §6.1: B4 800ms silence-timeout fallback (M-4). */
  armMouthFadeTimeout: (silence_timeout_ms: number, now_t: number) => void;
  /** v2 PRD §6.1: cancel pending/in-flight mouth fade (new viseme arrived). */
  cancelMouthFade: () => void;
  /** v2 PRD §6.1: B3 main path — push a single viseme frame. */
  setVisemeFrame: (frame: import("../pet-anim/visemeLipsync").VisemeFrame) => void;
  /** v2 PRD §6.1: B3 fallback — bulk-load estimated viseme stream. */
  setPhonemeEstimatorReady: (
    stream: import("../pet-anim/visemeLipsync").VisemeFrame[],
    now_t: number,
  ) => void;
  /** v2 PRD §6.1: flush viseme queue on tts_end. */
  flushVisemeQueue: () => void;
  /** v2 PRD §6.1: D1 emotion lock setter. */
  setEmotion: (
    emotion: import("../pet-anim/emotionMapper").EmotionCode,
    now_t: number,
  ) => void;
  /** v2 PRD §6.1: C1 low_energy state. */
  setLowEnergy: (active: boolean, now_t: number) => void;
  /** v2 PRD §6.1: C2 welcome pulse trigger. */
  triggerWelcome: (
    intensity: import("../pet-anim/idleWatcher").WelcomeIntensity,
    now_t: number,
  ) => void;
  /** v2 PRD §6.1: E1 edge attached state. */
  setEdgeAttached: (
    edge: import("../pet-anim/edgeWatcher").Edge,
    now_t: number,
  ) => void;
  /** v2 PRD §6.1: F1 DND state with reasons. */
  setDNDActive: (
    active: boolean,
    reasons: import("../pet-anim/dndDetector").DNDReason[],
    now_t: number,
  ) => void;
  /** v2 PRD §6.1: AC-10-03 — red supervisor severity (DND must not suppress). */
  setRedAlertActive: (active: boolean) => void;
  /** v2 PRD §6.1: C3 / D2 celebration trigger (3s happy_intense + TapBody). */
  triggerCelebration: (
    kind: "hourly" | "anniversary" | "milestone",
    message: string,
    now_t: number,
  ) => void;
  /** v2 PRD §6.1: full v2 debug surface. */
  getV2Debug: () => ReturnType<AnimationOverlay["getV2Debug"]>;
  /** 2026-05-31 fun: pointer down on the pet (begins drag/longPress/burst). */
  funPointerDown: (clientX: number, clientY: number, now_t: number) => void;
  /** 2026-05-31 fun: pointer move during hold (drag kinematics). */
  funPointerMove: (clientX: number, clientY: number, now_t: number) => void;
  /** 2026-05-31 fun: pointer up — ends drag, returns burst classification. */
  funPointerUp: (now_t: number) => {
    burst_count: number;
    burst_intensity: import("../pet-anim/funInteractions").TapBurstIntensity;
    double_tap: boolean;
  };
  /** 2026-05-31 fun: any user activity (chat input / app focus / etc.). */
  funMarkInteraction: (now_t: number) => void;
}

interface FaceFrame {
  left: number;
  top: number;
  width: number;
  height: number;
  face_center_x: number;
  face_center_y: number;
  face_radius_css: number;
}

/**
 * Compute hit-zone bounding box + face centre + radius from window
 * geometry + pet rendering width. PRD §6.0 v3 single source of truth —
 * the same values feed both the <div data-pet-hitzone> style and
 * overlay.setFaceCenter so they never drift.
 */
function computeFaceFrame(
  petWidth: number,
  innerHeight: number,
  innerWidth: number,
  modelScaleFactor = 1,
): FaceFrame {
  // 2026-05-31 fun-ux: 扩大 hit-zone 覆盖整个角色可见区（头顶→脚），
  // 之前只覆盖脸+躯干中间 50%×60% 的窄带，用户点裙子/腿/头发/手臂都没
  // 反应。现在覆盖角色整列宽 × 几乎全高，点哪都能触发交互。
  // face_center 仍锁在脸部（用于 gaze 凝视 + proximity/shy/dizzy 计算）。
  const left = innerWidth - petWidth;
  const width = Math.max(40, petWidth * modelScaleFactor);
  const top = innerHeight * 0.05;
  const height = Math.max(40, innerHeight * 0.9 * modelScaleFactor);
  return {
    left,
    top,
    width,
    height,
    // 脸约在角色立绘的上部 ~22% 处（Hiyori 全身站姿）。
    face_center_x: left + width / 2,
    face_center_y: top + height * 0.22,
    // face_radius 用角色宽的一半（脸 + 周边的合理凝视/害羞判定半径）。
    face_radius_css: Math.max(60, width * 0.5),
  };
}

/**
 * Live2D character rendered via PixiJS WebGL → <img> tag.
 * Falls back to Canvas2D animated cat if Live2D fails.
 *
 * WebView2 transparent windows don't composite <canvas>/<WebGL>.
 * We render offscreen and display each frame via <img> (HTML = composites OK).
 *
 * v3 (2026-05-24) — wires AnimationOverlay for FR-1~FR-7:
 *   - Render loop calls overlay.applyTo(coreModel, timestamp) instead of
 *     hand-rolled blink/tilt code
 *   - window-level pointermove → overlay.setGazeTarget (PRD §6.0)
 *   - <div data-pet-hitzone> covers face+torso → overlay.pulseInteraction
 *   - ResizeObserver + window resize keep face_center synced (PRD §6.0)
 *   - toBlob callback records visual_latency (PRD §6.8 FIFO pairing)
 */
export const Live2DCanvas = forwardRef<Live2DHandle, Live2DCanvasProps>(function Live2DCanvas(
  { modelPath, onFpsUpdate, mouthOpenY = 0, petWidth },
  ref,
) {
  const imgRef = useRef<HTMLImageElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const hitZoneRef = useRef<HTMLDivElement>(null);
  // FIX-R3: tracks pointerdown position for manual drag detection on
  // hit-zone. We avoid `data-tauri-drag-region` because it eats click.
  const dragStartRef = useRef<{ x: number; y: number } | null>(null);
  // FIX-R3 (round-3 retest): startDragging MUST be called synchronously
  // while the mouse button is still pressed. `await import()` adds enough
  // latency that the user releases the button first → SendMessage
  // WM_NCLBUTTONDOWN is a no-op. We pre-load on mount and cache the
  // bound function so the threshold-trigger call is synchronous.
  const startDraggingRef = useRef<(() => Promise<unknown>) | null>(null);
  const [size, setSize] = useState(() => ({
    // 跨 DPI 裁切修复：列宽 cap 在视口内（详见下方 apply 注释）。
    w: Math.min(petWidth ?? window.innerWidth, window.innerWidth),
    h: window.innerHeight,
    // 2026-06-03 跨 DPI 修复：把 devicePixelRatio 纳入 size 状态，使画布 resize
    // effect 能在「拖到不同缩放显示器(逻辑尺寸不变但 dpr 变)」时重跑。
    dpr: window.devicePixelRatio || 1,
  }));
  // 2026-06-03 跨 DPI 裁切修复：模型异步加载完成的信号。Live2D 模型 load 是
  // 异步的，加载完时 size 往往没变 → 画布 resize effect（依赖 size.*）不会重跑
  // → 画布停在 init() 摆放的尺寸/位置，没按当前 size.w(已 cap)+dpr 校正 →
  // 直接 boot 在某显示器时角色被裁。把它纳入 resize effect 的 deps，模型 ready
  // 后必触发一次正确的 renderer.resize + 模型 scale/centering。
  const [modelReady, setModelReady] = useState(false);
  // Use ref for mode to avoid re-render killing the render loop. We don't
  // expose this as React state because the render loop captures it once and
  // toggling it would otherwise tear down + re-init the whole pipeline.
  const modeRef = useRef<"loading" | "live2d" | "canvas2d">("loading");
  const cleanupRef = useRef<(() => void) | null>(null);
  const mouthRef = useRef(mouthOpenY);
  mouthRef.current = mouthOpenY;
  // Live2D model instance (set once init() succeeds). Kept on a ref so that
  // imperative methods can reach it without blowing up the render loop via
  // re-renders.
  const modelRef = useRef<any>(null);
  // 2026-06-03 跨 DPI 裁切修复：模型的**基础(未缩放)**宽高。pixi 的 model.width
  // 返回的是「当前 scale × localBounds」即**已缩放**宽，且渲染后才更新；resize
  // effect 若用它算 scale 会把已缩放宽当基础宽 → scale≈1 → 模型爆炸放大(只剩
  // 胸口)。故加载时存一次基础宽高，scale 计算一律用它。
  const modelBaseRef = useRef<{ w: number; h: number } | null>(null);
  // 2026-05-31: PixiJS Application instance ref. Needed so the size-change
  // effect (line ~390) can call pixiApp.renderer.resize() + recompute model
  // scale/position when the user resizes the host window. Without this the
  // PixiJS canvas stays at its mount-time physical size and the <img>
  // CSS-stretches the snapshot → character looks distorted ("人物被拉伸").
  const pixiAppRef = useRef<any>(null);
  // v3: AnimationOverlay instance — per Live2DCanvas mount. dispose()
  // called from cleanupRef so HMR / StrictMode double-mount can't leak.
  const overlayRef = useRef<AnimationOverlay | null>(null);
  // v3: face frame state. Updated by ResizeObserver + window resize + once
  // on model load. Drives both hit-zone DOM and overlay.setFaceCenter via
  // a single source `computeFaceFrame`.
  const faceFrameRef = useRef<FaceFrame>(
    computeFaceFrame(petWidth ?? window.innerWidth, window.innerHeight, window.innerWidth),
  );

  // Construct overlay once. We construct it eagerly so imperative handle
  // methods can no-op-write into it even before the Live2D model loads.
  if (overlayRef.current === null) {
    overlayRef.current = new AnimationOverlay({
      motionLabelsLoader: get_calibrated_motion_pools as () =>
        | Record<MotionTag, number[]>
        | null,
    });
  }

  useImperativeHandle(
    ref,
    () => ({
      setExpression(name: string) {
        const model = modelRef.current;
        if (!model) return;
        try {
          model.expression?.(name);
        } catch (err) {
          console.warn("[Live2D] setExpression failed:", name, err);
        }
      },
      playMotion(group: string) {
        const model = modelRef.current;
        if (!model) return;
        try {
          model.motion?.(group, undefined, 2);
        } catch (err) {
          console.warn("[Live2D] playMotion failed:", group, err);
        }
      },
      setBlinkRate(hz: number) {
        overlayRef.current?.setBlinkHz(Math.max(0, Number.isFinite(hz) ? hz : 0));
      },
      setHeadTilt(degrees: number) {
        const clamped = Math.max(-15, Math.min(15, Number.isFinite(degrees) ? degrees : 0));
        overlayRef.current?.setStateBaseHeadTilt(clamped);
      },
      setIdleSubset(_motionIds: string[]) {
        // Reserved for future models with multiple Idle groups.
        // Hiyori has only one ("Idle"), so no-op.
      },
      setMotionTagPool(tags, opts, now_t) {
        overlayRef.current?.setMotionTagPool(tags, opts, now_t);
      },
      setGazeTarget(clientX, clientY, now_t) {
        overlayRef.current?.setGazeTarget(clientX, clientY, now_t);
      },
      clearGazeTarget(now_t) {
        overlayRef.current?.clearGazeTarget(now_t);
      },
      pulseInteraction(kind) {
        overlayRef.current?.pulseInteraction(kind, performance.now());
      },
      getAnimationMetrics() {
        return (
          overlayRef.current?.getAnimationMetrics() ?? {
            interaction: { p50: 0, p95: 0, max: 0, samples: [] },
            visual: { p50: 0, p95: 0, max: 0, samples: [] },
          }
        );
      },
      getAnimationDebug() {
        return (
          overlayRef.current?.getAnimationDebug() ?? {
            gaze_target_yaw: 0,
            gaze_smoothed_yaw: 0,
            last_input_age_ms: 0,
            current_state: "rest",
            current_motion_idx: null,
          }
        );
      },
      // ───────── v2 setters ─────────
      setDragState(state, now_t) {
        overlayRef.current?.setDragState(state, now_t);
      },
      setUserInputActive(active, now_t) {
        overlayRef.current?.setUserInputActive(active, now_t);
      },
      setThinkingActive(active, now_t) {
        overlayRef.current?.setThinkingActive(active, now_t);
      },
      fadeMouthToZero(duration_ms, now_t) {
        overlayRef.current?.fadeMouthToZero(duration_ms, now_t);
      },
      armMouthFadeTimeout(silence_timeout_ms, now_t) {
        overlayRef.current?.armMouthFadeTimeout(silence_timeout_ms, now_t);
      },
      cancelMouthFade() {
        overlayRef.current?.cancelMouthFade();
      },
      setVisemeFrame(frame) {
        overlayRef.current?.setVisemeFrame(frame);
      },
      setPhonemeEstimatorReady(stream, now_t) {
        overlayRef.current?.setPhonemeEstimatorReady(stream, now_t);
      },
      flushVisemeQueue() {
        overlayRef.current?.flushVisemeQueue();
      },
      setEmotion(emotion, now_t) {
        overlayRef.current?.setEmotion(emotion, now_t);
      },
      setLowEnergy(active, now_t) {
        overlayRef.current?.setLowEnergy(active, now_t);
      },
      triggerWelcome(intensity, now_t) {
        overlayRef.current?.triggerWelcome(intensity, now_t);
      },
      setEdgeAttached(edge, now_t) {
        overlayRef.current?.setEdgeAttached(edge, now_t);
      },
      setDNDActive(active, reasons, now_t) {
        overlayRef.current?.setDNDActive(active, reasons, now_t);
      },
      setRedAlertActive(active) {
        overlayRef.current?.setRedAlertActive(active);
      },
      triggerCelebration(kind, message, now_t) {
        overlayRef.current?.triggerCelebration(kind, message, now_t);
      },
      getV2Debug() {
        return (
          overlayRef.current?.getV2Debug() ?? {
            held_state: "idle" as const,
            held_wobble_deg: 0,
            held_surprise: 0,
            user_input_active: false,
            thinking_active: false,
            mouth_fade_mode: "idle" as const,
            current_emotion: "neutral" as const,
            viseme_queue_size: 0,
            low_energy: false,
            welcome_active: false,
            welcome_intensity: "normal" as const,
            edge_attached: null,
            dnd_active: false,
            dnd_reasons: [],
            celebration_active: false,
            red_alert_active: false,
          }
        );
      },
      // 2026-05-31 fun interactions
      funPointerDown(clientX, clientY, now_t) {
        overlayRef.current?.funPointerDown(clientX, clientY, now_t);
      },
      funPointerMove(clientX, clientY, now_t) {
        overlayRef.current?.funPointerMove(clientX, clientY, now_t);
      },
      funPointerUp(now_t) {
        return (
          overlayRef.current?.funPointerUp(now_t) ?? {
            burst_count: 0,
            burst_intensity: "look_up" as const,
            double_tap: false,
          }
        );
      },
      funMarkInteraction(now_t) {
        overlayRef.current?.funMarkInteraction(now_t);
      },
    }),
    [],
  );

  // Track viewport. Also keep face frame current.
  useEffect(() => {
    const apply = (): void => {
      // 2026-06-03 跨 DPI 裁切修复：petWidth 是固定角色列宽(282)，但某些机器的
      // webview devicePixelRatio 高于显示器缩放（实测 dpr 2.13/1.42，可能叠加了
      // Windows 文本缩放 142%）→ 视口 innerWidth 仅 ~253 CSS < 282 → 角色 <img>
      // 比视口宽 → 右侧被裁、显示不全。把列宽 cap 在视口内：innerWidth≥petWidth 时
      // 仍用 petWidth(解耦不变)，否则收敛到 innerWidth → 角色完整可见(代价：略小)。
      const w = Math.min(petWidth ?? window.innerWidth, window.innerWidth);
      const h = window.innerHeight;
      const dpr = window.devicePixelRatio || 1;
      // 2026-06-03 跨 DPI 诊断：每次 viewport/dpr 变化都记一行，便于真机拖动时
      // 观察 innerWidth/dpr 随显示器切换的实际值（排查角色裁切）。
      console.warn(`[Pet] viewport: ${window.innerWidth} x ${window.innerHeight} dpr: ${dpr} petWidth: ${petWidth} size.w: ${w}`);
      setSize({ w, h, dpr });
      const ff = computeFaceFrame(w, h, window.innerWidth);
      faceFrameRef.current = ff;
      overlayRef.current?.setFaceCenter(ff.face_center_x, ff.face_center_y, ff.face_radius_css);
    };
    apply();
    let timeout: number | undefined;
    const throttled = (): void => {
      if (timeout) return;
      timeout = window.setTimeout(() => {
        timeout = undefined;
        apply();
      }, 100);
    };
    window.addEventListener("resize", throttled);
    let ro: ResizeObserver | null = null;
    if (typeof ResizeObserver !== "undefined" && containerRef.current) {
      ro = new ResizeObserver(throttled);
      ro.observe(containerRef.current);
    }
    // 2026-06-03 跨 DPI 修复：拖到不同缩放显示器时 devicePixelRatio 变化，但窗口
    // 逻辑尺寸不变 → resize 事件不一定触发 → 画布 renderer 卡在旧 DPR → 角色被裁。
    // matchMedia(resolution) 是 DPR 变化的可靠信号；每次变化后用新 DPR 重新 arm。
    let mql: MediaQueryList | null = null;
    const onDprChange = (): void => {
      throttled();
      armDpr();
    };
    const armDpr = (): void => {
      if (typeof window.matchMedia !== "function") return;
      mql?.removeEventListener("change", onDprChange);
      mql = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
      mql.addEventListener("change", onDprChange);
    };
    armDpr();
    return () => {
      window.removeEventListener("resize", throttled);
      if (timeout) window.clearTimeout(timeout);
      ro?.disconnect();
      mql?.removeEventListener("change", onDprChange);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [petWidth]);

  // 2026-05-31 restore — react to size changes: resize the Pixi renderer's
  // backing buffer AND recompute the model's scale + centering so the
  // character doesn't get squashed when the window aspect ratio shifts.
  // Without this, init() picks one renderW/renderH and sticks with it;
  // any later resize just stretches the same backing texture via CSS,
  // producing the left/right pinch the user reported after dragging the
  // window wider.
  useEffect(() => {
    const app = pixiAppRef.current;
    const model = modelRef.current;
    if (!app || !model) return;
    // 2026-06-03 跨 DPI 修复：用 size.dpr（随显示器切换更新）而非裸读
    // window.devicePixelRatio；deps 含 size.dpr → DPR 变化即重算 renderer 尺寸 +
    // 模型 scale/position，否则画布卡在旧 DPR 物理尺寸 → 角色被裁/拉伸。
    const dpr = size.dpr || window.devicePixelRatio || 1;
    const renderW = Math.round(size.w * dpr);
    const renderH = Math.round(size.h * dpr);
    console.warn(`[Live2D] renderer resize -> ${renderW}x${renderH} (size ${size.w}x${size.h} dpr ${dpr})`);
    try {
      app.renderer?.resize?.(renderW, renderH);
    } catch (e) {
      console.warn("[Live2D] renderer.resize failed:", e);
    }
    // Equal-aspect rescale: Math.min keeps the model proportionally
    // sized — wider window → more breathing room, never horizontal
    // stretch. 用**基础**宽高(modelBaseRef)算 scale + 居中，绝不能用
    // model.width/height（那是已缩放宽 → scale≈1 → 模型爆炸放大）。
    const baseW = modelBaseRef.current?.w ?? model.width;
    const baseH = modelBaseRef.current?.h ?? model.height;
    const scaleX = (renderW * 0.85) / baseW;
    const scaleY = (renderH * 0.7) / baseH;
    const scale = Math.min(scaleX, scaleY);
    model.scale.set(scale);
    model.x = (renderW - baseW * scale) / 2;
    model.y = (renderH - baseH * scale) * 0.25;
  }, [size.w, size.h, size.dpr, modelReady]);

  // FIX-R3: pre-load Tauri window startDragging so the manual drag
  // handler can call it synchronously during the gesture.
  useEffect(() => {
    let cancelled = false;
    import("@tauri-apps/api/window")
      .then((m) => {
        if (cancelled) return;
        try {
          const w = m.getCurrentWindow();
          startDraggingRef.current = () => w.startDragging();
        } catch {
          /* ignore — Tauri runtime unavailable (dev-browser preview) */
        }
      })
      .catch(() => {
        /* ignore */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Window-level pointermove for gaze. PRD §6.0 v3: even with
  // ignore_cursor_events=true the WebView JS still receives this
  // (Day-0 Probe-3); listener lives here so a hit-zone-bounded fallback
  // can be slotted in by flipping the addEventListener target later.
  useEffect(() => {
    const overlay = overlayRef.current;
    if (!overlay) return;
    const onMove = (e: PointerEvent): void => {
      overlay.setGazeTarget(e.clientX, e.clientY, e.timeStamp);
      // 2026-05-31 fun: feed shy-away + circle-dizzy observers.
      overlay.funCursorMove?.(e.clientX, e.clientY, e.timeStamp);
    };
    const onBlur = (): void => {
      overlay.clearGazeTarget(performance.now());
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("blur", onBlur);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("blur", onBlur);
    };
  }, []);

  // Expose metrics + debug + bench on window for ManualTest helpers.
  useEffect(() => {
    const w = window as unknown as Record<string, unknown>;
    w["__deskpet_anim_metrics"] = () => overlayRef.current?.getAnimationMetrics();
    w["__deskpet_anim_debug"] = overlayRef.current?.getAnimationDebug();
    // Keep debug pointer fresh — read from overlay each access via getter.
    Object.defineProperty(w, "__deskpet_anim_debug", {
      configurable: true,
      get: () => overlayRef.current?.getAnimationDebug(),
    });
    if (import.meta.env.DEV) {
      const model = modelRef.current;
      w["__deskpet_anim_bench"] = {
        applyToOnce: (t: number) => {
          const m = modelRef.current ?? model;
          if (m && overlayRef.current) {
            overlayRef.current.applyTo((m as any).internalModel?.coreModel as CoreModelLike, t);
          }
        },
      };
      // v2 observability bridge for ManualTest §0.2 helpers (round-1 FAIL fix).
      // Exposes the AnimationOverlay instance + v2 debug surface to DevTools/CDP
      // so manual tests can call setEmotion / setDragState / etc. directly and
      // read getV2Debug() without going through the React tree.
      Object.defineProperty(w, "__deskpet_anim_overlay", {
        configurable: true,
        get: () => overlayRef.current,
      });
      Object.defineProperty(w, "__deskpet_anim_debug_v2", {
        configurable: true,
        get: () => overlayRef.current?.getV2Debug(),
      });
    }
    return () => {
      try {
        delete w["__deskpet_anim_metrics"];
        delete w["__deskpet_anim_debug"];
        delete w["__deskpet_anim_bench"];
        delete w["__deskpet_anim_overlay"];
        delete w["__deskpet_anim_debug_v2"];
      } catch {
        /* ignore */
      }
    };
  }, []);

  // 2026-06-03: 此处原有**第二个**「resize PixiJS renderer + reposition」effect，
  // 与上方(~460)那个职责完全重复，但用 window.devicePixelRatio + model.width
  // (已缩放宽)→ 拖动时它后跑、覆盖上方修好的结果 → 模型 scale≈1 爆炸放大
  // (用户实测：拖动后角色只剩胸口)。已删除，统一由上方那个(size.dpr + 基础
  // 宽高 modelBaseRef + modelReady)负责，单一权威路径，杜绝双 effect 竞争。

  // Main init — runs once
  useEffect(() => {
    if (modeRef.current !== "loading") return;

    let destroyed = false;
    let pixiApp: any = null;
    let rafId = 0;

    async function init() {
      try {
        console.warn("[Live2D] starting PixiJS...");
        const PIXI = await import("pixi.js");
        (window as any).PIXI = PIXI;

        if (destroyed) return;

        const dpr = window.devicePixelRatio || 1;
        const renderW = Math.round(size.w * dpr);
        const renderH = Math.round(size.h * dpr);

        pixiApp = new PIXI.Application({
          width: renderW,
          height: renderH,
          backgroundAlpha: 0,
          antialias: true,
          preserveDrawingBuffer: true,
          resolution: 1,
        });

        if (destroyed) { pixiApp.destroy(true); return; }
        // Expose for the resize-rescale effect.
        pixiAppRef.current = pixiApp;

        try {
          pixiApp.stage.eventMode = "none";
          pixiApp.stage.interactiveChildren = false;
          pixiApp.renderer?.events?.destroy?.();
        } catch { /* ignore */ }

        document.querySelectorAll<HTMLCanvasElement>("canvas[data-pet-live2d]")
          .forEach((stale) => {
            try { stale.parentNode?.removeChild(stale); } catch { /* ignore */ }
          });
        const pixiCanvas = pixiApp.view as HTMLCanvasElement;
        pixiCanvas.setAttribute("data-pet-live2d", "1");
        pixiCanvas.style.cssText = "position:fixed;top:-9999px;left:-9999px;pointer-events:none;";
        document.body.appendChild(pixiCanvas);

        console.warn("[Live2D] PixiJS created, loading cubism4...");
        const { Live2DModel } = await import("pixi-live2d-display/cubism4");
        if (destroyed) return;

        console.warn("[Live2D] loading model:", modelPath);
        const model = await Promise.race([
          Live2DModel.from(modelPath),
          new Promise((_, rej) => setTimeout(() => rej(new Error("timeout 15s")), 15000)),
        ]) as any;

        if (destroyed) return;
        console.warn("[Live2D] model loaded:", model.width, "x", model.height);
        // 此刻 model.width/height 尚未被下方 scale.set 改动 = 基础尺寸，存下来供
        // resize effect 复用（避免它读到已缩放宽 → 误算 scale）。
        modelBaseRef.current = { w: model.width, h: model.height };

        model.autoInteract = false;

        const scaleX = (renderW * 0.85) / model.width;
        const scaleY = (renderH * 0.7) / model.height;
        const scale = Math.min(scaleX, scaleY);
        model.scale.set(scale);
        model.x = (renderW - model.width * scale) / 2;
        model.y = (renderH - model.height * scale) * 0.25;

        pixiApp.stage.addChild(model);
        modelRef.current = model;
        // 模型就绪 → 触发 resize effect 跑一次，用当前 size.w(已 cap)+dpr
        // 正确设定画布尺寸与模型 scale/居中（init 的初值可能不匹配实际视口）。
        setModelReady(true);

        // Inject motion player into the overlay so motionPool/FR-5 can
        // drive real Idle/TapBody groups.
        overlayRef.current?.setMotionPlayer((group, idx) => {
          try {
            model.motion?.(group, idx, 2);
          } catch (err) {
            console.warn("[Live2D] motion player failed:", group, idx, err);
          }
        });
        // Push the freshly-loaded model into the face-frame computation.
        const ff = faceFrameRef.current;
        overlayRef.current?.setFaceCenter(ff.face_center_x, ff.face_center_y, ff.face_radius_css);

        try {
          (window as any).__deskpet_play_motion = (group: string, idx?: number) => {
            try {
              model.motion?.(group, idx, 2);
            } catch (err) {
              console.warn("[Live2D] indexed motion failed:", group, idx, err);
            }
          };
        } catch { /* ignore */ }

        modeRef.current = "live2d";
        console.warn("[Live2D] render loop starting");

        const TARGET_FPS = 30;
        const FRAME_INTERVAL = 1000 / TARGET_FPS;
        let frameCount = 0;
        let lastFpsTime = performance.now();
        let lastFrameTime = 0;
        let pendingBlob = false;
        let currentBlobUrl: string | null = null;

        function renderLoop(timestamp: number) {
          if (destroyed) return;

          const delta = timestamp - lastFrameTime;
          if (delta >= FRAME_INTERVAL && !pendingBlob) {
            lastFrameTime = timestamp - (delta % FRAME_INTERVAL);
            frameCount++;

            const now = performance.now();
            if (now - lastFpsTime >= 1000) {
              onFpsUpdate?.(Math.round((frameCount * 1000) / (now - lastFpsTime)));
              frameCount = 0;
              lastFpsTime = now;
            }

            // v3: hand control to AnimationOverlay. mouth_open_y is the
            // only legacy param still pushed in-place here; everything
            // else (blink, perlin, gaze, saccade, tilt) is overlay-owned.
            try {
              const coreModel = (model as any).internalModel?.coreModel as
                | CoreModelLike
                | undefined;
              if (coreModel && overlayRef.current) {
                overlayRef.current.setMouthOpenY(mouthRef.current);
                overlayRef.current.applyTo(coreModel, timestamp);
              }
            } catch { /* ignore if model structure differs */ }

            if (imgRef.current && pixiApp?.view) {
              pendingBlob = true;
              try {
                (pixiApp.view as HTMLCanvasElement).toBlob(
                  (blob: Blob | null) => {
                    pendingBlob = false;
                    if (destroyed || !blob || !imgRef.current) return;
                    if (currentBlobUrl) URL.revokeObjectURL(currentBlobUrl);
                    currentBlobUrl = URL.createObjectURL(blob);
                    imgRef.current.src = currentBlobUrl;
                    // v3: record visual latency — pair this frame with
                    // the oldest pending click event (FIFO per §3.8).
                    overlayRef.current?.recordVisualFrameTs(performance.now());
                  },
                  "image/webp",
                  0.8,
                );
              } catch {
                pendingBlob = false;
              }
            }
          }

          rafId = requestAnimationFrame(renderLoop);
        }
        rafId = requestAnimationFrame(renderLoop);

      } catch (err) {
        console.warn("[Live2D] failed:", err);
        try {
          const pixiCanvas = pixiApp?.view as HTMLCanvasElement;
          if (pixiCanvas?.parentNode) pixiCanvas.parentNode.removeChild(pixiCanvas);
          pixiApp?.destroy(true);
        } catch { /* ignore */ }
        pixiApp = null;

        if (!destroyed) {
          modeRef.current = "canvas2d";
          startCanvas2D();
        }
      }
    }

    // Canvas2D fallback — unchanged from pre-v3, just a fallback character.
    function startCanvas2D() {
      console.warn("[Canvas2D] starting fallback");
      const width = size.w;
      const height = size.h;
      const canvas = document.createElement("canvas");
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      const rawCtx = canvas.getContext("2d");
      if (!rawCtx) return;
      const ctx: CanvasRenderingContext2D = rawCtx;

      let frameCount = 0;
      let lastFpsTime = performance.now();
      let eyeBlinkTimer = 0;
      let isBlinking = false;
      const cs = Math.min(width / 300, height / 450, 1);

      function draw(ts: number) {
        if (destroyed) return;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, width, height);

        frameCount++;
        const now = performance.now();
        if (now - lastFpsTime >= 1000) {
          onFpsUpdate?.(Math.round((frameCount * 1000) / (now - lastFpsTime)));
          frameCount = 0;
          lastFpsTime = now;
        }

        ctx.save();
        ctx.translate(width / 2, height * 0.38);
        ctx.scale(cs, cs);
        const by = Math.sin(ts / 1500) * 3, bo = Math.sin(ts / 800) * 2;

        ctx.fillStyle = "rgba(0,0,0,0.15)";
        ctx.beginPath(); ctx.ellipse(0, 140 + by, 55, 10, 0, 0, Math.PI * 2); ctx.fill();
        const tw = Math.sin(ts / 300) * 15;
        ctx.strokeStyle = "rgba(99,102,241,0.8)"; ctx.lineWidth = 8; ctx.lineCap = "round";
        ctx.beginPath(); ctx.moveTo(45, 85 + by); ctx.quadraticCurveTo(75 + tw, 55 + by, 70 + tw * 1.5, 25 + by); ctx.stroke();
        ctx.fillStyle = "rgba(99,102,241,0.9)"; roundRect(ctx, -55, 50 + by, 110, 80, 22);
        ctx.fillStyle = "rgba(129,140,248,0.3)"; roundRect(ctx, -40, 55 + by, 80, 25, 12);
        ctx.fillStyle = "rgba(129,140,248,0.9)";
        ctx.beginPath(); ctx.ellipse(-35, 130 + by, 18, 10, -0.1, 0, Math.PI * 2); ctx.fill();
        ctx.beginPath(); ctx.ellipse(35, 130 + by, 18, 10, 0.1, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = "rgba(99,102,241,0.95)";
        ctx.beginPath(); ctx.arc(0, bo, 65, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = "rgba(99,102,241,0.95)";
        ctx.beginPath(); ctx.moveTo(-55, -20 + bo); ctx.lineTo(-70, -70 + bo); ctx.lineTo(-25, -45 + bo); ctx.closePath(); ctx.fill();
        ctx.beginPath(); ctx.moveTo(55, -20 + bo); ctx.lineTo(70, -70 + bo); ctx.lineTo(25, -45 + bo); ctx.closePath(); ctx.fill();
        ctx.fillStyle = "rgba(196,181,253,0.7)";
        ctx.beginPath(); ctx.moveTo(-52, -25 + bo); ctx.lineTo(-63, -60 + bo); ctx.lineTo(-32, -42 + bo); ctx.closePath(); ctx.fill();
        ctx.beginPath(); ctx.moveTo(52, -25 + bo); ctx.lineTo(63, -60 + bo); ctx.lineTo(32, -42 + bo); ctx.closePath(); ctx.fill();
        eyeBlinkTimer += 16;
        if (eyeBlinkTimer > 3000 && !isBlinking) { isBlinking = true; eyeBlinkTimer = 0; }
        if (isBlinking && eyeBlinkTimer > 150) { isBlinking = false; eyeBlinkTimer = 0; }
        const ey = -8 + bo, eo = isBlinking ? 0.1 : 1;
        ctx.fillStyle = "#fff";
        ctx.beginPath(); ctx.ellipse(-24, ey, 16, 18 * eo, 0, 0, Math.PI * 2); ctx.fill();
        ctx.beginPath(); ctx.ellipse(24, ey, 16, 18 * eo, 0, 0, Math.PI * 2); ctx.fill();
        if (!isBlinking) {
          const px = Math.sin(ts / 2000) * 4, py = Math.cos(ts / 3000) * 2;
          ctx.fillStyle = "#1e1b4b";
          ctx.beginPath(); ctx.arc(-24 + px, ey + py, 8, 0, Math.PI * 2); ctx.fill();
          ctx.beginPath(); ctx.arc(24 + px, ey + py, 8, 0, Math.PI * 2); ctx.fill();
          ctx.fillStyle = "rgba(255,255,255,0.9)";
          ctx.beginPath(); ctx.arc(-20 + px, ey - 4 + py, 4, 0, Math.PI * 2); ctx.fill();
          ctx.beginPath(); ctx.arc(28 + px, ey - 4 + py, 3, 0, Math.PI * 2); ctx.fill();
        }
        ctx.fillStyle = "rgba(251,191,207,0.45)";
        ctx.beginPath(); ctx.ellipse(-42, 12 + bo, 14, 8, 0, 0, Math.PI * 2); ctx.fill();
        ctx.beginPath(); ctx.ellipse(42, 12 + bo, 14, 8, 0, 0, Math.PI * 2); ctx.fill();
        const mOpen = mouthRef.current;
        ctx.fillStyle = "rgba(196,181,253,0.8)";
        ctx.beginPath(); ctx.moveTo(0, 8 + bo); ctx.lineTo(-5, 14 + bo); ctx.lineTo(5, 14 + bo); ctx.closePath(); ctx.fill();
        if (mOpen > 0.05) {
          ctx.fillStyle = "rgba(67,56,202,0.6)";
          ctx.beginPath(); ctx.ellipse(0, 20 + bo, 8, 4 + mOpen * 10, 0, 0, Math.PI * 2); ctx.fill();
        } else {
          ctx.strokeStyle = "#4338ca"; ctx.lineWidth = 2; ctx.lineCap = "round";
          ctx.beginPath(); ctx.arc(-8, 16 + bo, 8, -0.3, Math.PI * 0.7); ctx.stroke();
          ctx.beginPath(); ctx.arc(8, 16 + bo, 8, Math.PI * 0.3, Math.PI + 0.3); ctx.stroke();
        }
        ctx.strokeStyle = "rgba(200,200,220,0.5)"; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.moveTo(-30, 10 + bo); ctx.lineTo(-65, 5 + bo); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(-30, 16 + bo); ctx.lineTo(-65, 18 + bo); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(30, 10 + bo); ctx.lineTo(65, 5 + bo); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(30, 16 + bo); ctx.lineTo(65, 18 + bo); ctx.stroke();
        ctx.restore();

        canvas.toBlob(
          (blob) => {
            if (!blob || !imgRef.current || destroyed) return;
            if (c2dBlobUrl) URL.revokeObjectURL(c2dBlobUrl);
            c2dBlobUrl = URL.createObjectURL(blob);
            imgRef.current.src = c2dBlobUrl;
          },
          "image/webp",
          0.8,
        );
        rafId = requestAnimationFrame(draw);
      }
      let c2dBlobUrl: string | null = null;
      rafId = requestAnimationFrame(draw);
    }

    init();

    cleanupRef.current = () => {
      destroyed = true;
      cancelAnimationFrame(rafId);
      modelRef.current = null;
      pixiAppRef.current = null;
      setModelReady(false);
      // v3: dispose the overlay so HMR + StrictMode unmounts don't leak.
      overlayRef.current?.dispose();
      overlayRef.current = null;
      try {
        const pixiCanvas = pixiApp?.view as HTMLCanvasElement;
        if (pixiCanvas?.parentNode) pixiCanvas.parentNode.removeChild(pixiCanvas);
        pixiApp?.destroy(true);
      } catch { /* ignore */ }
    };

    return () => {
      cleanupRef.current?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Run once only

  // hit-zone DOM (PRD §6.0). Sized in absolute CSS px from
  // computeFaceFrame so it stays in sync with overlay's face_center.
  // z-index is intentionally below HiyoriMotionTuner / SettingsPanel /
  // other UI surfaces (which all use 100+) so they remain clickable on
  // top — see CASE-PR-06.
  const ff = faceFrameRef.current;
  return (
    <>
      <div ref={containerRef} style={{ display: "none" }} />
      <img
        ref={imgRef}
        alt=""
        style={{
          width: `${size.w}px`,
          height: `${size.h}px`,
          position: "absolute",
          top: 0,
          right: 0,
          pointerEvents: "none",
        }}
      />
      <div
        ref={hitZoneRef}
        data-pet-hitzone="1"
        style={{
          position: "fixed",
          left: ff.left,
          top: ff.top,
          width: ff.width,
          height: ff.height,
          pointerEvents: "auto",
          background: "transparent",
          // FIX-R3 (2026-05-24): z-index 25→5. With z=25 the hit-zone
          // covered the upper portion of DialogBar (z:10) at the face
          // bbox's bottom edge, eating mousedown and preventing text
          // selection on assistant replies. Dropping to z:5 keeps
          // hit-zone above the pet <img> (pointer-events:none anyway)
          // but BELOW DialogBar (z:10), input bar (z:20),
          // PetDebugOverlay (z:30), Toolbar and other UI surfaces —
          // anything visible on top now wins pointer events, while the
          // empty face area still triggers hit-zone reactions.
          zIndex: 5,
        }}
        onPointerEnter={(e) => {
          overlayRef.current?.pulseInteraction("hover_enter", e.timeStamp);
        }}
        onPointerLeave={(e) => {
          overlayRef.current?.pulseInteraction("hover_leave", e.timeStamp);
        }}
        onPointerDown={(e) => {
          // FIX-R3: manual drag detection. We can't use
          // `data-tauri-drag-region` because that attribute triggers
          // Win32 WM_NCLBUTTONDOWN on mousedown — the OS then owns the
          // gesture and React's onClick never fires (so the TapBody
          // pulse is lost). Instead we record the down point and watch
          // for movement > DRAG_THRESHOLD_PX; if it exceeds, we
          // explicitly call appWindow.startDragging(). A pure
          // mousedown+up without movement falls through to onClick
          // (preserving the click pulse).
          dragStartRef.current = { x: e.clientX, y: e.clientY };
          // 2026-05-31 fun: kick off drag/longPress/burst observer.
          overlayRef.current?.funPointerDown(e.clientX, e.clientY, e.timeStamp);
        }}
        onPointerMove={(e) => {
          // 2026-05-31 fun: feed pointermove into drag kinematics (only when
          // pressed — funPointerDown sets ctx.active true; sample is no-op
          // when inactive).
          overlayRef.current?.funPointerMove(e.clientX, e.clientY, e.timeStamp);
          const start = dragStartRef.current;
          if (!start) return;
          const dx = e.clientX - start.x;
          const dy = e.clientY - start.y;
          if (dx * dx + dy * dy > 25 /* 5px threshold squared */) {
            dragStartRef.current = null;
            // v2 A1: hand the overlay into being_held so wobble + surprise fire
            // for the duration of the drag. Cleared on pointerup below.
            overlayRef.current?.setDragState("being_held", e.timeStamp);
            // Synchronous call — the cached startDragging was prepared
            // at mount, so SendMessage WM_NCLBUTTONDOWN runs before the
            // user can release the button.
            const fn = startDraggingRef.current;
            if (fn) {
              try {
                void fn();
              } catch {
                /* ignore */
              }
            }
          }
        }}
        onPointerUp={(e) => {
          dragStartRef.current = null;
          // v2 A1: tell overlay drag ended → spring_back begins.
          overlayRef.current?.setDragState("idle", e.timeStamp);
          // 2026-05-31 fun: ends drag + classify tap burst.
          overlayRef.current?.funPointerUp(e.timeStamp);
        }}
        onPointerCancel={(e) => {
          dragStartRef.current = null;
          overlayRef.current?.setDragState("idle", e.timeStamp);
          overlayRef.current?.funPointerUp(e.timeStamp);
        }}
        onClick={(e) => {
          const ts = e.timeStamp;
          const overlay = overlayRef.current;
          if (!overlay) return;
          const before = performance.now();
          overlay.pulseInteraction("click", ts);
          overlay.recordInteractionLatency(performance.now() - before);
        }}
      />
    </>
  );
});

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number, y: number, w: number, h: number, r: number,
) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
  ctx.fill();
}
