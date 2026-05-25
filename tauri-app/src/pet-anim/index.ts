/**
 * AnimationOverlay (pet-anim) — TDD §2.10 / §3.6 / §3.7 / §3.8.
 *
 * Single per-Live2DCanvas-instance singleton that consumes the 9 pet-anim
 * sub-modules and produces a 6-step per-frame parameter write sequence.
 *
 * Per-frame write order (TDD §3.6) — motion3 has already updated the
 * coreModel before applyTo runs, so all operations layer on top:
 *
 *   step 1: SET ParamMouthOpenY = mouth                                (lip-sync)
 *   step 2: ADD ParamAngleX/Y/BodyAngleX += perlin(t) × 3 dims         (degrees)
 *   step 3: ADD ParamAngleX += gaze.head_yaw_deg                       (degrees)
 *           ADD ParamAngleY += gaze.head_pitch_deg                     (degrees)
 *   step 4: ADD ParamEyeBallX += gaze.eye_yaw_norm + saccade.offset_x  (normalised)
 *           ADD ParamEyeBallY += gaze.eye_pitch_norm + saccade.offset_y
 *   step 5: MULTIPLY ParamEyeLOpen *= blink.multiplier                  (set get*mul)
 *           MULTIPLY ParamEyeROpen *= blink.multiplier
 *   step 6: SET ParamAngleZ = state_base_tilt + active_transient_delta (degrees)
 *
 * Fallback paths (TDD §3.7):
 *   - coreModel missing → applyTo returns silently
 *   - addParameterValueByIndex undefined → fallback to set(idx, get(idx) + delta)
 *   - getParameterIndex returns -1 → that parameter is silently skipped
 *
 * Latency pairing (TDD §3.8 / PRD §6.8) — FIFO queue, cap=20.
 *   recordInteractionEventTs pushes;
 *   recordVisualFrameTs shifts the queue head and records (frame_ts - event_ts).
 *
 * Per-instance HMR-safety:
 *   dispose() marks the overlay disposed → subsequent applyTo is a no-op.
 */
import { createPerlin1D, type PerlinFn } from './perlinNoise'
import { createBlinkScheduler, type BlinkScheduler, type BlinkState } from './blinkScheduler'
import {
  createSaccadeScheduler,
  type SaccadeScheduler,
  type SaccadeState,
} from './saccadeScheduler'
import {
  createGazeTracker,
  type GazeTracker,
  type GazeState,
} from './gazeTracking'
import { createMotionPicker, type MotionPicker, type MotionPickerState, type MotionTag } from './motionPicker'
import {
  createMotionScheduler,
  type MotionScheduler,
  type MotionSchedulerState,
} from './motionScheduler'
import {
  createPointerReactor,
  type PointerReactor,
  type ReactorContext,
  type InteractionKind,
} from './pointerReaction'
import { createMetricsRing, type MetricsRing, type MetricsSnapshot } from './metricsRing'
import { isEnabled, type AnimFlag } from './featureFlags'
import {
  createDragStateMachine,
  type DragContext,
  type DragState,
  type DragStateMachine,
} from './heldStateMachine'
import { createMouthFader, type MouthFader } from './mouthFader'
import {
  createVisemeLipsync,
  type VisemeLipsync,
  type VisemeFrame,
} from './visemeLipsync'
import { getEmotionParams, type EmotionCode } from './emotionMapper'
import type { WelcomeIntensity } from './idleWatcher'
import { poseForEdge, type Edge } from './edgeWatcher'
import type { DNDReason } from './dndDetector'

export type { MotionTag, InteractionKind, DragState, EmotionCode, WelcomeIntensity, VisemeFrame, Edge, DNDReason }

export interface CoreModelLike {
  getParameterIndex(name: string): number
  setParameterValueByIndex(idx: number, val: number): void
  addParameterValueByIndex?(idx: number, val: number): void
  getParameterValueByIndex(idx: number): number
}

export interface OverlayOpts {
  rng?: () => number
  storage?: Storage
  /** Pluggable loader so tests don't depend on global PetStateMachine state. */
  motionLabelsLoader?: () => Record<MotionTag, number[]> | null
  /** Override the perlin amplitude (PRD OQ-3 A/B/C calibration). */
  perlin_amplitude_deg?: number
  /** Override the perlin frequency (Hz). */
  perlin_frequency_hz?: number
  /** Default motion group when picking. */
  motion_group_name?: string
}

export type MotionPlayer = (group: string, idx: number) => void

const LATENCY_QUEUE_CAP = 20

const PERLIN_SEEDS = { angle_x: 1337, angle_y: 2741, body_angle_x: 4099 }

interface PendingClickEntry {
  kind: 'click' | 'double_click'
  event_ts: number
}

interface TransientTilt {
  delta_deg: number
  end_t: number
}

export class AnimationOverlay {
  // ───────── sub-module instances ─────────
  private perlinAngleX: PerlinFn
  private perlinAngleY: PerlinFn
  private perlinBodyAngleX: PerlinFn
  private blink: BlinkScheduler
  private blinkState: BlinkState | null = null
  private saccade: SaccadeScheduler
  private saccadeState: SaccadeState
  private gaze: GazeTracker
  private gazeState: GazeState
  private picker: MotionPicker
  private pickerState: MotionPickerState
  private scheduler: MotionScheduler
  private schedulerState: MotionSchedulerState | null = null
  private reactor: PointerReactor
  private reactorCtx: ReactorContext
  private interaction_metrics: MetricsRing
  private visual_metrics: MetricsRing

  // ───────── caller-controlled state ─────────
  private blink_hz: number = 0.2
  private state_base_tilt_deg: number = 0
  private transient_tilt: TransientTilt | null = null
  private mouth_open_y: number = 0
  private active_tags: MotionTag[] = []
  private current_motion_idx: number | null = null
  private motion_player: MotionPlayer | null = null
  private motion_group_name: string

  // ───────── ADD path resolution (Day-0 Probe-1 effect) ─────────
  private add_path_resolved = false
  private add_native = false
  private fallback_warned = false

  // ───────── latency pairing FIFO ─────────
  private pending_clicks: PendingClickEntry[] = []

  // ───────── lifecycle ─────────
  private disposed = false
  private storage: Storage | undefined
  private motionLabelsLoader: () => Record<MotionTag, number[]> | null

  // ───────── v2 state (Pet Animation UX v2) ─────────
  /** A1 wobble/spring FSM. */
  private heldFSM: DragStateMachine
  private heldCtx: DragContext
  /** Latest wobble degrees from heldFSM tick (re-used across step 6 + step 7). */
  private last_wobble_deg = 0
  /** A1 surprise factor 0..1 (decays linearly over surprise_duration_ms). */
  private last_surprise_factor = 0

  /** B1 user-input observer state — driven by external observer; we only store the active flag here. */
  private b1_active = false

  /** B2 thinking active flag — driven by external observer. */
  private b2_active = false

  /** B4 mouth fade controller — sample() is consulted in step 1. */
  private mouthFader: MouthFader
  /** Tracks the last mouth_open_y written to model (for B4 800ms fallback origin). */
  private last_mouth_observed = 0

  /** B3 viseme lipsync (main path). Frames pushed via setVisemeFrame/setPhonemeEstimatorReady. */
  private visemeLipsync: VisemeLipsync
  /** Tracks whether the viseme queue is actively driving the mouth (gates step-9 MouthForm conflict). */
  private b3_active_this_frame = false

  /** D1 current locked emotion. Lock release is caller-driven (see Live2DCanvas / App.tsx). */
  private current_emotion: EmotionCode = 'neutral'

  /** C1 low_energy active flag (drives blink_hz / breath rate / motion tag pool overrides). */
  private c1_low_energy = false
  /** C2 welcome state: happy boost until welcome_active_until ms, current intensity. */
  private c2_welcome_active_until = -Infinity
  private c2_welcome_intensity: WelcomeIntensity = 'normal'
  /** C2 intense path: 2nd TapBody at +800ms after first. -Infinity if not pending. */
  private c2_second_tap_at = -Infinity

  /** E1 edge attached state — null when not attached. */
  private e1_edge: Edge = null

  /** F1 DND active state + reasons. */
  private f1_dnd_active = false
  private f1_dnd_reasons: DNDReason[] = []

  /** D2/C3 celebration state — when active_until > now, treat as happy_intense. */
  private celebration_active_until = -Infinity
  /** Marker: red supervisor alert active (AC-10-03 — DND must NOT suppress). */
  private red_alert_active = false

  constructor(opts: OverlayOpts = {}) {
    const rng = opts.rng ?? Math.random
    this.storage = opts.storage
    this.motionLabelsLoader = opts.motionLabelsLoader ?? (() => null)
    this.motion_group_name = opts.motion_group_name ?? 'Idle'

    const amplitude = opts.perlin_amplitude_deg ?? 2.0
    const frequency = opts.perlin_frequency_hz ?? 0.3
    this.perlinAngleX = createPerlin1D({ seed: PERLIN_SEEDS.angle_x, amplitude, frequency })
    this.perlinAngleY = createPerlin1D({ seed: PERLIN_SEEDS.angle_y, amplitude, frequency })
    this.perlinBodyAngleX = createPerlin1D({
      seed: PERLIN_SEEDS.body_angle_x,
      amplitude,
      frequency,
    })

    this.blink = createBlinkScheduler({ blink_hz: this.blink_hz, rng })
    this.saccade = createSaccadeScheduler({ rng })
    this.saccadeState = this.saccade.init(0)
    this.gaze = createGazeTracker()
    this.gazeState = this.gaze.init()
    this.picker = createMotionPicker({ rng })
    this.pickerState = this.picker.init()
    this.scheduler = createMotionScheduler({ switch_period_ms: 15_000, rng })
    this.reactor = createPointerReactor()
    this.reactorCtx = this.reactor.init()
    this.interaction_metrics = createMetricsRing(100)
    this.visual_metrics = createMetricsRing(100)

    // v2: A1/B4/B3 internal sub-machines.
    this.heldFSM = createDragStateMachine()
    this.heldCtx = this.heldFSM.init()
    this.mouthFader = createMouthFader()
    this.visemeLipsync = createVisemeLipsync({ blend_ms: 60 })
  }

  dispose(): void {
    this.disposed = true
    this.motion_player = null
    this.pending_clicks = []
    // v2 cleanup
    this.heldCtx = this.heldFSM.init()
    this.mouthFader.cancel()
    this.visemeLipsync.flush()
    this.b1_active = false
    this.b2_active = false
    this.current_emotion = 'neutral'
    this.c1_low_energy = false
    this.c2_welcome_active_until = -Infinity
    this.c2_second_tap_at = -Infinity
  }

  // ───────── v2 setters (PRD §6.1) ─────────

  /**
   * A1: Drag state machine input. Callers feed 'being_held' on drag start (>5px
   * movement after pointerdown) and 'idle' on drag end. The 'spring_back'
   * intermediate state is owned by the FSM — callers do not pass it directly.
   */
  setDragState(state: DragState, now_t: number): void {
    if (!Number.isFinite(now_t)) return
    if (state === 'being_held' && this.heldCtx.state !== 'being_held') {
      const r = this.heldFSM.onDragStart(this.heldCtx, now_t)
      this.heldCtx = r.ctx
      this.last_wobble_deg = r.wobble_delta
      this.last_surprise_factor = r.surprise_factor
    } else if (state === 'idle' && this.heldCtx.state === 'being_held') {
      const r = this.heldFSM.onDragEnd(this.heldCtx, now_t)
      this.heldCtx = r.ctx
      this.last_wobble_deg = r.wobble_delta
      this.last_surprise_factor = r.surprise_factor
    } else if (state === 'idle') {
      // Force reset (covers edge cases like spring_back → external cancel).
      this.heldCtx = this.heldFSM.init()
      this.last_wobble_deg = 0
      this.last_surprise_factor = 0
    }
  }

  /** B1: User-input observer callback wiring. */
  setUserInputActive(active: boolean, _now_t: number): void {
    this.b1_active = !!active
  }

  /** B2: Thinking observer callback wiring. */
  setThinkingActive(active: boolean, _now_t: number): void {
    this.b2_active = !!active
  }

  /** B4: Start a deterministic mouth fade now (caller observed tts_end). */
  fadeMouthToZero(duration_ms: number, now_t: number): void {
    // Origin priority:
    //   1. last_mouth_observed if it has been written in a previous frame
    //   2. mouth_open_y (the static input value, in case applyTo hasn't run yet)
    const origin = this.last_mouth_observed > 0 ? this.last_mouth_observed : this.mouth_open_y
    this.mouthFader.start(origin, duration_ms, now_t)
  }

  /** B4: Arm the 800ms silence-timeout fallback. */
  armMouthFadeTimeout(silence_timeout_ms: number, now_t: number): void {
    this.mouthFader.noteCurrentMouth(this.last_mouth_observed)
    this.mouthFader.startWithTimeout(silence_timeout_ms, now_t)
  }

  /** B3 (will be used in S2): cancels any pending mouth fade because a new viseme arrived. */
  cancelMouthFade(): void {
    this.mouthFader.cancel()
  }

  /** B3 main path: queue a single viseme frame for lipsync. Caller passes absolute t_ms. */
  setVisemeFrame(frame: VisemeFrame): void {
    this.visemeLipsync.push(frame)
    // A new viseme always cancels any pending mouth fade — B3 takes over the mouth.
    this.mouthFader.cancel()
  }

  /** B3 fallback path: bulk-load an estimated frame stream (phonemeEstimator output). */
  setPhonemeEstimatorReady(stream: VisemeFrame[], _now_t: number): void {
    if (!Array.isArray(stream)) return
    this.visemeLipsync.pushMany(stream)
    this.mouthFader.cancel()
  }

  /** B3: flush the viseme queue (e.g. on tts_end). The mouth fader will take over for B4. */
  flushVisemeQueue(): void {
    this.visemeLipsync.flush()
  }

  /** D1: lock current emotion. Caller releases by passing 'neutral'. */
  setEmotion(emotion: EmotionCode, _now_t: number): void {
    this.current_emotion = emotion
  }

  /** C1: enter / leave low_energy. Affects blink_hz + motion tag pool + breath. */
  setLowEnergy(active: boolean, now_t: number): void {
    if (active === this.c1_low_energy) return
    this.c1_low_energy = active
    if (active) {
      this.setBlinkHz(0.1)
      // Caller (Live2DCanvas / App.tsx) is responsible for setMotionTagPool(['low-energy','slow','yawn']);
      // we keep that decision outside the overlay so callers can compose with their own pools.
    } else {
      // Restore default blink_hz (caller may override with another setBlinkHz).
      this.setBlinkHz(0.2)
    }
    // No need to use now_t for state changes — kept in signature for parity.
    void now_t
  }

  /** E1: caller decided the pet is attached to a screen edge — applies SET pose. */
  setEdgeAttached(edge: Edge, _now_t: number): void {
    this.e1_edge = edge
  }

  /** F1: DND active state with reasons (used by step 2/3/4 force-block + AngleX/Y/Z force 0). */
  setDNDActive(active: boolean, reasons: DNDReason[], _now_t: number): void {
    this.f1_dnd_active = !!active
    this.f1_dnd_reasons = Array.isArray(reasons) ? reasons.slice() : []
  }

  /** AC-10-03: supervisor red severity is independent of DND — must not be suppressed. */
  setRedAlertActive(active: boolean): void {
    this.red_alert_active = !!active
  }

  /**
   * C3 / D2 celebration trigger: 3s happy_intense with TapBody. Bubble UI is
   * managed by App.tsx (PetCelebrationBubble component).
   */
  triggerCelebration(_kind: 'hourly' | 'anniversary' | 'milestone', _message: string, now_t: number): void {
    if (!Number.isFinite(now_t)) return
    this.celebration_active_until = now_t + 3000
    if (this.motion_player) {
      try {
        this.motion_player('TapBody', 0)
      } catch {
        /* swallow */
      }
    }
  }

  /**
   * C2: trigger a welcome pulse. Fires immediate happy params + TapBody.
   * Duration: 1500ms (normal/bubble) or 3000ms (intense). Intense also schedules
   * a second TapBody at +800ms (tracked via internal flag, fired in applyTo).
   */
  triggerWelcome(intensity: WelcomeIntensity, now_t: number): void {
    if (!Number.isFinite(now_t)) return
    const duration_ms = intensity === 'intense' ? 3000 : 1500
    this.c2_welcome_active_until = now_t + duration_ms
    this.c2_welcome_intensity = intensity
    if (this.motion_player) {
      try {
        this.motion_player('TapBody', 0)
      } catch {
        /* swallow */
      }
    }
    if (intensity === 'intense') {
      this.c2_second_tap_at = now_t + 800
    } else {
      this.c2_second_tap_at = -Infinity
    }
  }

  /** Debug snapshot for v2 instrumentation. */
  getV2Debug(): {
    held_state: DragState
    held_wobble_deg: number
    held_surprise: number
    user_input_active: boolean
    thinking_active: boolean
    mouth_fade_mode: 'idle' | 'pending' | 'fading'
    current_emotion: EmotionCode
    viseme_queue_size: number
    low_energy: boolean
    welcome_active: boolean
    welcome_intensity: WelcomeIntensity
    edge_attached: Edge
    dnd_active: boolean
    dnd_reasons: DNDReason[]
    celebration_active: boolean
    red_alert_active: boolean
  } {
    return {
      held_state: this.heldCtx.state,
      held_wobble_deg: this.last_wobble_deg,
      held_surprise: this.last_surprise_factor,
      user_input_active: this.b1_active,
      thinking_active: this.b2_active,
      mouth_fade_mode: this.mouthFader.debug().mode,
      current_emotion: this.current_emotion,
      viseme_queue_size: this.visemeLipsync.debug().queue_size,
      low_energy: this.c1_low_energy,
      welcome_active: this.c2_welcome_active_until > 0,
      welcome_intensity: this.c2_welcome_intensity,
      edge_attached: this.e1_edge,
      dnd_active: this.f1_dnd_active,
      dnd_reasons: this.f1_dnd_reasons.slice(),
      celebration_active: this.celebration_active_until > 0,
      red_alert_active: this.red_alert_active,
    }
  }

  // ───────── supervisor → overlay ─────────
  setBlinkHz(hz: number): void {
    const safe = Math.max(0, Number.isFinite(hz) ? hz : 0)
    if (safe === this.blink_hz) return
    this.blink_hz = safe
    // Re-construct blink scheduler with the new rate (state is invalidated).
    this.blink = createBlinkScheduler({ blink_hz: safe, rng: Math.random })
    this.blinkState = null
  }

  setStateBaseHeadTilt(deg: number): void {
    if (!Number.isFinite(deg)) return
    // Cubism Hiyori ParamAngleZ comfortably handles ±15°; clamp defensively.
    this.state_base_tilt_deg = Math.max(-15, Math.min(15, deg))
  }

  pulseHeadTiltDelta(deg: number, duration_ms: number, now_t: number): void {
    if (!Number.isFinite(deg) || !Number.isFinite(duration_ms) || duration_ms <= 0) return
    this.transient_tilt = {
      delta_deg: deg,
      end_t: now_t + duration_ms,
    }
  }

  setMouthOpenY(v: number): void {
    this.mouth_open_y = Math.max(0, Math.min(1, Number.isFinite(v) ? v : 0))
  }

  // ───────── motion driving ─────────
  setMotionPlayer(player: MotionPlayer | null): void {
    this.motion_player = player
  }

  setMotionTagPool(
    tags: MotionTag[],
    opts: { force_switch_now: boolean },
    now_t: number,
  ): void {
    this.active_tags = tags
    if (this.schedulerState === null) {
      this.schedulerState = this.scheduler.init(now_t)
    }
    if (opts.force_switch_now) {
      const next = this.scheduler.forceSwitchNow(this.schedulerState, now_t).state
      this.schedulerState = next
      this.tryPickAndPlay(now_t)
    }
  }

  private resolveCandidates(): number[] {
    if (this.active_tags.length === 0) return []
    const labels = this.motionLabelsLoader()
    if (!labels) return []
    const candidates = new Set<number>()
    for (const tag of this.active_tags) {
      const arr = labels[tag]
      if (Array.isArray(arr)) for (const idx of arr) candidates.add(idx)
    }
    // PRD §FR-5 OQ-4 fallback: size < 2 → bring in medium, then everything.
    if (candidates.size < 2 && labels.medium) {
      for (const idx of labels.medium) candidates.add(idx)
    }
    if (candidates.size < 2) {
      for (const tag of ['fast', 'medium', 'slow', 'special'] as MotionTag[]) {
        for (const idx of labels[tag] ?? []) candidates.add(idx)
      }
    }
    return Array.from(candidates).sort((a, b) => a - b)
  }

  private tryPickAndPlay(now_t: number): void {
    const candidates = this.resolveCandidates()
    const r = this.picker.pick(this.pickerState, candidates)
    this.pickerState = r.state
    if (r.idx !== null) {
      this.current_motion_idx = r.idx
      if (this.motion_player) {
        try {
          this.motion_player(this.motion_group_name, r.idx)
        } catch {
          /* ignore — debug surface, don't crash render loop */
        }
      }
    }
    if (this.schedulerState !== null) {
      this.schedulerState = this.scheduler.scheduleNext(this.schedulerState, now_t).state
    }
  }

  // ───────── gaze ─────────
  setFaceCenter(cx_css: number, cy_css: number, radius_css: number): void {
    this.gazeState = this.gaze.setFaceFrame(this.gazeState, cx_css, cy_css, radius_css)
  }

  setGazeTarget(clientX: number, clientY: number, now_t: number): void {
    if (!Number.isFinite(clientX) || !Number.isFinite(clientY)) return
    this.gazeState = this.gaze.setTarget(this.gazeState, clientX, clientY, now_t)
  }

  clearGazeTarget(now_t: number): void {
    this.gazeState = this.gaze.clearTarget(this.gazeState, now_t)
  }

  // ───────── pointer reaction ─────────
  pulseInteraction(kind: InteractionKind, now_t: number): void {
    switch (kind) {
      case 'hover_enter': {
        const r = this.reactor.onPointerEnter(this.reactorCtx, now_t)
        this.reactorCtx = r.ctx
        if (r.effect === 'hover_enter') {
          // Bump blink rate transiently + tiny head tilt.
          this.pulseHeadTiltDelta(3, 200, now_t)
        }
        break
      }
      case 'hover_leave': {
        const r = this.reactor.onPointerLeave(this.reactorCtx, now_t)
        this.reactorCtx = r.ctx
        break
      }
      case 'click': {
        const r = this.reactor.onClick(this.reactorCtx, now_t)
        this.reactorCtx = r.ctx
        // Record latency event for any click attempt (FIFO will pair later).
        this.recordInteractionEventTs('click', now_t)
        if (r.effect === 'click' || r.effect === 'double_click') {
          this.applyClickEffect(r.effect, now_t)
        }
        break
      }
      case 'double_click': {
        // Some callers (testClickPair) emit double_click synthetically;
        // route through onClick twice so the FSM stays the source of truth.
        const r1 = this.reactor.onClick(this.reactorCtx, now_t)
        this.reactorCtx = r1.ctx
        const r2 = this.reactor.onClick(this.reactorCtx, now_t + 1)
        this.reactorCtx = r2.ctx
        this.recordInteractionEventTs('double_click', now_t)
        if (r2.effect === 'double_click') this.applyClickEffect('double_click', now_t)
        break
      }
    }
  }

  private applyClickEffect(kind: 'click' | 'double_click', now_t: number): void {
    if (kind === 'click') {
      this.pulseHeadTiltDelta(5, 200, now_t)
      if (this.motion_player) {
        try {
          this.motion_player('TapBody', 0)
        } catch {
          /* ignore */
        }
      }
    } else {
      this.pulseHeadTiltDelta(10, 400, now_t)
      // Spike blink for excitement; will fade back when caller sets next rate.
      if (this.motion_player) {
        try {
          this.motion_player('TapBody', 0)
        } catch {
          /* ignore */
        }
      }
    }
  }

  // ───────── per-frame apply ─────────
  applyTo(model: CoreModelLike, now_t: number): void {
    if (this.disposed) return
    if (!model) return
    if (!isEnabled('all' as AnimFlag, this.storage)) return

    // Tick reactor (handles pulse expiry).
    const tickRes = this.reactor.tick(this.reactorCtx, now_t)
    this.reactorCtx = tickRes.ctx
    if (tickRes.effect === 'click') {
      this.applyClickEffect('click', now_t)
    }

    // Resolve ADD path once (per Day-0 Probe-1).
    if (!this.add_path_resolved) {
      this.add_native = typeof model.addParameterValueByIndex === 'function'
      this.add_path_resolved = true
      if (!this.add_native && !this.fallback_warned) {
        console.warn('[pet-anim] addParameterValueByIndex missing — using set/get fallback')
        this.fallback_warned = true
      }
    }
    const addParam = (name: string, delta: number): void => {
      if (!Number.isFinite(delta) || delta === 0) return
      const idx = model.getParameterIndex(name)
      if (idx < 0) return
      if (this.add_native && model.addParameterValueByIndex) {
        model.addParameterValueByIndex(idx, delta)
      } else {
        const cur = model.getParameterValueByIndex(idx)
        model.setParameterValueByIndex(idx, cur + delta)
      }
    }
    const setParam = (name: string, val: number): void => {
      if (!Number.isFinite(val)) return
      const idx = model.getParameterIndex(name)
      if (idx < 0) return
      model.setParameterValueByIndex(idx, val)
    }
    const mulParam = (name: string, mul: number): void => {
      if (!Number.isFinite(mul)) return
      const idx = model.getParameterIndex(name)
      if (idx < 0) return
      const cur = model.getParameterValueByIndex(idx)
      model.setParameterValueByIndex(idx, cur * mul)
    }

    // ───────── v2 pre-step: tick A1 held FSM (sets last_wobble/surprise for this frame) ─────────
    const v2On = isEnabled('v2_all', this.storage)
    if (v2On && (this.heldCtx.state === 'being_held' || this.heldCtx.state === 'spring_back')) {
      const r = this.heldFSM.tick(this.heldCtx, now_t)
      this.heldCtx = r.ctx
      this.last_wobble_deg = r.wobble_delta
      this.last_surprise_factor = r.surprise_factor
    } else if (!v2On) {
      // v2 hard kill: ensure no v2 state leaks into v1 frames.
      this.last_wobble_deg = 0
      this.last_surprise_factor = 0
    }
    const v2HeldActive =
      v2On && isEnabled('held', this.storage) && this.heldCtx.state === 'being_held'
    // F1 DND active path: blocks Perlin/gaze/saccade, forces Angle{X,Y,Z}=0, lowers blink_hz.
    // AC-10-03: red alert ignores DND below (it's surfaced by the supervisor independently).
    const v2DNDActive = v2On && isEnabled('dnd', this.storage) && this.f1_dnd_active
    const blocksTransients = v2HeldActive || v2DNDActive

    // ───────── step 1: mouth — priority §6.2 matrix L524: DND > B4 fade > B3 viseme > D1 surprised ─────────
    let mouth_to_write = this.mouth_open_y
    let viseme_mouth_form: number | null = null
    this.b3_active_this_frame = false

    // B3 viseme: read first; if active, override mouth + capture form.
    if (v2On && isEnabled('viseme', this.storage)) {
      const vs = this.visemeLipsync.sample(now_t)
      if (vs.v !== 'silent') {
        mouth_to_write = vs.mouthY
        viseme_mouth_form = vs.mouthForm
        this.b3_active_this_frame = true
      }
    }

    // B4 fade overrides B3 (matrix priority).
    if (v2On && isEnabled('mouth_fade', this.storage)) {
      const faded = this.mouthFader.sample(now_t)
      if (faded !== null) mouth_to_write = faded
    }

    // D1 surprised has MouthOpenY=0.4 — applies only if neither B3 nor B4 is active.
    if (
      v2On &&
      !this.b3_active_this_frame &&
      isEnabled('emotion', this.storage) &&
      this.current_emotion === 'surprised'
    ) {
      const emo = getEmotionParams('surprised')
      if (typeof emo.ParamMouthOpenY === 'number') {
        mouth_to_write = emo.ParamMouthOpenY
      }
    }

    setParam('ParamMouthOpenY', mouth_to_write)
    this.last_mouth_observed = mouth_to_write

    // ───────── step 2: perlin micro-motion (held active blocks per PRD §3 A1) ─────────
    if (isEnabled('perlin', this.storage) && !blocksTransients) {
      addParam('ParamAngleX', this.perlinAngleX(now_t))
      addParam('ParamAngleY', this.perlinAngleY(now_t))
      addParam('ParamBodyAngleX', this.perlinBodyAngleX(now_t))
    }

    // ───────── step 3 + 4: gaze + saccade (held active blocks both per PRD §3 A1) ─────────
    let gaze_head_yaw_deg = 0
    let gaze_head_pitch_deg = 0
    let eye_yaw_norm = 0
    let eye_pitch_norm = 0
    if (isEnabled('gaze', this.storage) && !blocksTransients) {
      const r = this.gaze.tick(this.gazeState, now_t)
      this.gazeState = r.state
      gaze_head_yaw_deg = r.head_yaw_deg
      gaze_head_pitch_deg = r.head_pitch_deg
      eye_yaw_norm = r.eye_yaw_norm
      eye_pitch_norm = r.eye_pitch_norm
      addParam('ParamAngleX', gaze_head_yaw_deg)
      addParam('ParamAngleY', gaze_head_pitch_deg)
    }
    let sac_x = 0
    let sac_y = 0
    if (isEnabled('saccade', this.storage) && !blocksTransients) {
      const r = this.saccade.tick(this.saccadeState, now_t)
      this.saccadeState = r.state
      sac_x = r.offset_x
      sac_y = r.offset_y
    }
    if ((isEnabled('gaze', this.storage) || isEnabled('saccade', this.storage)) && !blocksTransients) {
      addParam('ParamEyeBallX', eye_yaw_norm + sac_x)
      addParam('ParamEyeBallY', eye_pitch_norm + sac_y)
    }

    // ───────── step 5a: D1 EyeOpenMul (§6.2 matrix L529 chain: baseline × D1 × B1 × blink) ─────────
    if (v2On && isEnabled('emotion', this.storage) && this.current_emotion !== 'neutral') {
      const emo = getEmotionParams(this.current_emotion)
      if (typeof emo.ParamEyeLOpenMul === 'number' && emo.ParamEyeLOpenMul !== 1) {
        mulParam('ParamEyeLOpen', emo.ParamEyeLOpenMul)
      }
      if (typeof emo.ParamEyeROpenMul === 'number' && emo.ParamEyeROpenMul !== 1) {
        mulParam('ParamEyeROpen', emo.ParamEyeROpenMul)
      }
    }

    // ───────── step 5b: B1 eye-open boost (MUL chain after D1, before blink). ─────────
    if (v2On && isEnabled('user_input', this.storage) && this.b1_active) {
      mulParam('ParamEyeLOpen', 1.15)
      mulParam('ParamEyeROpen', 1.15)
    }

    // ───────── step 5: blink ─────────
    if (isEnabled('blink', this.storage) && this.blink_hz > 0) {
      if (this.blinkState === null) this.blinkState = this.blink.init(now_t)
      const r = this.blink.tick(this.blinkState, now_t)
      this.blinkState = r.state
      const mul = r.eye_open_multiplier
      if (mul !== 1) {
        mulParam('ParamEyeLOpen', mul)
        mulParam('ParamEyeROpen', mul)
      }
    }

    // ───────── step 6: head tilt = base + transient + A1 wobble + B1 tilt (§6.2 ParamAngleZ row) ─────────
    let transient = 0
    if (this.transient_tilt) {
      if (now_t <= this.transient_tilt.end_t) {
        transient = this.transient_tilt.delta_deg
      } else {
        this.transient_tilt = null
      }
    }
    let v2_angle_z_extra = 0
    if (v2On && isEnabled('held', this.storage)) {
      v2_angle_z_extra += this.last_wobble_deg
    }
    if (v2On && isEnabled('user_input', this.storage) && this.b1_active) {
      v2_angle_z_extra += 3
    }
    // E1 edge pose: SET overrides A1/B1/base — matrix §6.2 ParamAngleZ row.
    let angle_z = this.state_base_tilt_deg + transient + v2_angle_z_extra
    if (v2On && isEnabled('edge', this.storage) && this.e1_edge !== null) {
      angle_z = poseForEdge(this.e1_edge)
    }
    if (v2DNDActive) {
      // DND force 0 — matrix highest priority for AngleZ.
      angle_z = 0
    }
    // E1 / pose may exceed v1 ±15° clamp (180° upside-down) — use ±180 cap.
    setParam('ParamAngleZ', Math.max(-180, Math.min(180, angle_z)))

    // ───────── step 7: v2 additive head-X / eyeball-Y / body-Z / hair / brow / surprise ─────────
    if (v2On) {
      // A1: wobble also drives ParamBodyAngleZ (§6.2 matrix L536).
      if (isEnabled('held', this.storage) && this.last_wobble_deg !== 0) {
        addParam('ParamBodyAngleZ', this.last_wobble_deg)
      }
      // B1: HairFront oscillation (PRD §3 B1: amp 0.2, period 600ms).
      if (isEnabled('user_input', this.storage) && this.b1_active) {
        const hair = 0.2 * Math.sin((2 * Math.PI * now_t) / 600)
        addParam('ParamHairFront', hair)
      }
      // B2 thinking: head up + eyeball up + brow up (PRD §3 B2).
      if (isEnabled('thinking', this.storage) && this.b2_active && !v2DNDActive) {
        addParam('ParamAngleX', 5)
        addParam('ParamEyeBallY', 0.6)
        // B2 brow conflict with D1: PRD §6.2 matrix L534 says B2 > D1; SET is fine here in S1.
        setParam('ParamBrowLY', 0.3)
        setParam('ParamBrowRY', 0.3)
      }

      // F1 DND: force AngleX/Y to 0 (matrix highest priority).
      if (v2DNDActive) {
        setParam('ParamAngleX', 0)
        setParam('ParamAngleY', 0)
      }
      // ───────── step 8: D1 emotion face params (PRD §6.2 matrix step 9 SET set) ─────────
      // Priority order at this step: A1 surprise (below) > C2 welcome (intense) > D1 > B2 brow (set above).
      // B3 viseme already wrote MouthForm in step 1 path; we honour matrix priority "B3 > D1" for MouthForm.
      const welcomeActive = isEnabled('welcome', this.storage) && now_t < this.c2_welcome_active_until
      const celebrationActive =
        (isEnabled('time_celebration', this.storage) || isEnabled('milestone', this.storage)) &&
        now_t < this.celebration_active_until
      const effectiveEmotion: EmotionCode =
        welcomeActive || celebrationActive ? 'happy' : this.current_emotion
      if (isEnabled('emotion', this.storage) && effectiveEmotion !== 'neutral') {
        const emo = getEmotionParams(effectiveEmotion)
        if (typeof emo.ParamMouthForm === 'number' && !this.b3_active_this_frame) {
          // B3 > D1 for MouthForm — write only when B3 not active.
          setParam('ParamMouthForm', emo.ParamMouthForm)
        }
        if (typeof emo.ParamEyeLSmile === 'number') setParam('ParamEyeLSmile', emo.ParamEyeLSmile)
        if (typeof emo.ParamEyeRSmile === 'number') setParam('ParamEyeRSmile', emo.ParamEyeRSmile)
        if (typeof emo.ParamCheek === 'number') setParam('ParamCheek', emo.ParamCheek)
        // BrowLY/RY: D1 < B2 (B2 set above already); only write if B2 didn't.
        if (!(isEnabled('thinking', this.storage) && this.b2_active)) {
          if (typeof emo.ParamBrowLY === 'number') setParam('ParamBrowLY', emo.ParamBrowLY)
          if (typeof emo.ParamBrowRY === 'number') setParam('ParamBrowRY', emo.ParamBrowRY)
        }
        if (typeof emo.ParamBrowLAngle === 'number') setParam('ParamBrowLAngle', emo.ParamBrowLAngle)
        if (typeof emo.ParamBrowRAngle === 'number') setParam('ParamBrowRAngle', emo.ParamBrowRAngle)
        // D1 sad pitches head down — ADD to ParamAngleY (matrix L527).
        if (typeof emo.ParamAngleY === 'number') addParam('ParamAngleY', emo.ParamAngleY)
      }

      // ───────── step 8b: B3 viseme MouthForm (writes here, AFTER D1 emotion SET above, so it wins). ─────────
      if (this.b3_active_this_frame && viseme_mouth_form !== null) {
        setParam('ParamMouthForm', viseme_mouth_form)
      }

      // ───────── step 8c: C2 intense path — schedule a second TapBody at +800ms. ─────────
      if (isEnabled('welcome', this.storage) && this.c2_second_tap_at !== -Infinity && now_t >= this.c2_second_tap_at) {
        if (this.motion_player) {
          try {
            this.motion_player('TapBody', 0)
          } catch {
            /* swallow */
          }
        }
        this.c2_second_tap_at = -Infinity
      }

      // A1 surprise pulse: SET face params interpolated by surprise_factor (PRD §3 A1 surprise).
      // Note: this can override B2 brow values transiently — matches the "drag interrupts thinking" UX.
      if (isEnabled('held', this.storage) && this.last_surprise_factor > 0) {
        const sf = this.last_surprise_factor
        setParam('ParamMouthForm', -0.5 * sf)
        // EyeOpen surprise pulse: 1.0 → 1.3 modulated by surprise_factor
        const eyeOpenPulse = 1 + 0.3 * sf
        setParam('ParamEyeLOpen', eyeOpenPulse)
        setParam('ParamEyeROpen', eyeOpenPulse)
        setParam('ParamBrowLY', 0.5 * sf)
        setParam('ParamBrowRY', 0.5 * sf)
      }
    }

    // ───────── motion scheduling ─────────
    if (
      isEnabled('motionpool', this.storage) &&
      this.schedulerState &&
      this.scheduler.shouldSwitch(this.schedulerState, now_t)
    ) {
      this.tryPickAndPlay(now_t)
    }
  }

  // ───────── latency ─────────
  recordInteractionEventTs(kind: 'click' | 'double_click', event_ts: number): void {
    this.pending_clicks.push({ kind, event_ts })
    while (this.pending_clicks.length > LATENCY_QUEUE_CAP) this.pending_clicks.shift()
    // Interaction latency is event→applyTo: we treat the moment the
    // overlay was notified (now_t the caller used) as the apply time.
    // Caller passes event_ts but interaction record uses 0 (best-effort)
    // — actual measurement uses the helper below.
  }

  /**
   * For tests / callers that want to record a custom event→write delta
   * directly (Live2DCanvas wires this from its pointerEvent.timeStamp →
   * the next applyTo timestamp).
   */
  recordInteractionLatency(latency_ms: number): void {
    this.interaction_metrics.record(latency_ms)
  }

  recordVisualFrameTs(frame_ts: number): void {
    if (this.pending_clicks.length === 0) return
    const head = this.pending_clicks.shift() as PendingClickEntry
    this.visual_metrics.record(frame_ts - head.event_ts)
  }

  getAnimationMetrics(): { interaction: MetricsSnapshot; visual: MetricsSnapshot } {
    return {
      interaction: this.interaction_metrics.snapshot(),
      visual: this.visual_metrics.snapshot(),
    }
  }

  getAnimationDebug(): {
    gaze_target_yaw: number
    gaze_smoothed_yaw: number
    last_input_age_ms: number
    current_state: string
    current_motion_idx: number | null
  } {
    // NFR-6: last_input_t comes from event.timeStamp / performance.now()
    // (DOMHighResTimeStamp). Mixing with Date.now() (epoch ms) would
    // produce nonsensical ages (~56 years). Use performance.now() here
    // when available; fall back to Date.now() only in non-browser tests.
    const nowSameBase =
      typeof performance !== 'undefined' && typeof performance.now === 'function'
        ? performance.now()
        : Date.now()
    return {
      gaze_target_yaw: this.gazeState.target_yaw_deg,
      gaze_smoothed_yaw: this.gazeState.smoothed_yaw_deg,
      last_input_age_ms:
        this.gazeState.last_input_t === -Infinity
          ? Infinity
          : Math.max(0, nowSameBase - this.gazeState.last_input_t),
      current_state: this.reactorCtx.state,
      current_motion_idx: this.current_motion_idx,
    }
  }
}
