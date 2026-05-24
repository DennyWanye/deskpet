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

export type { MotionTag, InteractionKind }

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
  }

  dispose(): void {
    this.disposed = true
    this.motion_player = null
    this.pending_clicks = []
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

    // ───────── step 1: mouth ─────────
    setParam('ParamMouthOpenY', this.mouth_open_y)

    // ───────── step 2: perlin micro-motion ─────────
    if (isEnabled('perlin', this.storage)) {
      addParam('ParamAngleX', this.perlinAngleX(now_t))
      addParam('ParamAngleY', this.perlinAngleY(now_t))
      addParam('ParamBodyAngleX', this.perlinBodyAngleX(now_t))
    }

    // ───────── step 3 + 4: gaze + saccade ─────────
    let gaze_head_yaw_deg = 0
    let gaze_head_pitch_deg = 0
    let eye_yaw_norm = 0
    let eye_pitch_norm = 0
    if (isEnabled('gaze', this.storage)) {
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
    if (isEnabled('saccade', this.storage)) {
      const r = this.saccade.tick(this.saccadeState, now_t)
      this.saccadeState = r.state
      sac_x = r.offset_x
      sac_y = r.offset_y
    }
    if (isEnabled('gaze', this.storage) || isEnabled('saccade', this.storage)) {
      addParam('ParamEyeBallX', eye_yaw_norm + sac_x)
      addParam('ParamEyeBallY', eye_pitch_norm + sac_y)
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

    // ───────── step 6: head tilt = base + transient ─────────
    let transient = 0
    if (this.transient_tilt) {
      if (now_t <= this.transient_tilt.end_t) {
        transient = this.transient_tilt.delta_deg
      } else {
        this.transient_tilt = null
      }
    }
    const angle_z = Math.max(-15, Math.min(15, this.state_base_tilt_deg + transient))
    setParam('ParamAngleZ', angle_z)

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
    return {
      gaze_target_yaw: this.gazeState.target_yaw_deg,
      gaze_smoothed_yaw: this.gazeState.smoothed_yaw_deg,
      last_input_age_ms:
        this.gazeState.last_input_t === -Infinity
          ? Infinity
          : Math.max(0, Date.now() - this.gazeState.last_input_t),
      current_state: this.reactorCtx.state,
      current_motion_idx: this.current_motion_idx,
    }
  }
}
