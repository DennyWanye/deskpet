/**
 * idleWatcher (pet-anim/C1 + C2) — Pet Animation UX v2.
 *
 * Tracks global user-input activity timestamps and decides when the pet should
 * enter `low_energy` (PRD §3 C1) and emit a welcome pulse (PRD §3 C2).
 *
 * Algorithm
 *   - Caller calls `notifyActivity(state, now_t)` whenever an "input event"
 *     fires. PRD §3 C1 events list: keydown, mousemove, wheel, pointermove,
 *     visibilitychange, focus, blur. The wiring lives in App.tsx; this module
 *     is event-source-agnostic.
 *   - `tick(state, now_t)` checks whether the inactivity window has elapsed.
 *     If `state.low_energy === false` and (now_t − last_activity_t) ≥
 *     `low_energy_threshold_ms`, transitions to low_energy and fires
 *     `onLowEnergy(now_t)`.
 *   - When the next activity arrives while in low_energy, fires
 *     `onWakeup(now_t, low_energy_duration_ms)`. The caller chooses welcome
 *     intensity from the duration (PRD §3 C2 escalation table).
 *   - `welcome_cooldown_ms` debounces rapid sleep/wake cycles.
 *
 * State is pure (in → out); no internal mutable references.
 * NFR-6: every now_t is DOMHighResTimeStamp.
 *
 * Welcome intensity helper (per PRD §3 C2):
 *   < welcome_bubble_threshold_ms  →  'normal'   (TapBody + happy 1500ms)
 *   ≥ bubble_threshold,  < intense →  'bubble'   (above + "好久不见" 3s)
 *   ≥ intense_threshold            →  'intense'  (2× TapBody + bubble + 3s)
 */

export type WelcomeIntensity = 'normal' | 'bubble' | 'intense'

export interface IdleOpts {
  /** Inactivity to enter low_energy. Default 300000 (5min). */
  low_energy_threshold_ms?: number
  /** Cooldown after welcome before another welcome can fire. Default 60000. */
  welcome_cooldown_ms?: number
  /** 15min threshold for 'bubble' intensity. Default 900000. */
  welcome_bubble_threshold_ms?: number
  /** 1h threshold for 'intense' intensity. Default 3600000. */
  welcome_intense_threshold_ms?: number
}

export interface IdleWatcherState {
  /** Currently in low_energy. */
  low_energy: boolean
  /** Last user-input event timestamp. */
  last_activity_t: number
  /** When low_energy began. -Infinity if not low_energy. */
  low_energy_start_t: number
  /** Last fired welcome timestamp (for cooldown). */
  last_welcome_t: number
}

export interface IdleWatcherCallbacks {
  onLowEnergy?: (now_t: number) => void
  /** Fires immediately when input returns from low_energy. */
  onWakeup?: (
    now_t: number,
    low_energy_duration_ms: number,
    intensity: WelcomeIntensity,
  ) => void
}

export interface IdleWatcher {
  init(): IdleWatcherState
  notifyActivity(s: IdleWatcherState, now_t: number): IdleWatcherState
  tick(s: IdleWatcherState, now_t: number): IdleWatcherState
}

const DEFAULTS: Required<IdleOpts> = {
  low_energy_threshold_ms: 300_000,
  welcome_cooldown_ms: 60_000,
  welcome_bubble_threshold_ms: 900_000,
  welcome_intense_threshold_ms: 3_600_000,
}

function safeNum(v: number | undefined, fallback: number, mustBePositive = false): number {
  if (v === undefined || !Number.isFinite(v)) return fallback
  if (mustBePositive && v <= 0) return fallback
  return v
}

export function pickWelcomeIntensity(
  duration_ms: number,
  bubble_threshold_ms: number = DEFAULTS.welcome_bubble_threshold_ms,
  intense_threshold_ms: number = DEFAULTS.welcome_intense_threshold_ms,
): WelcomeIntensity {
  if (duration_ms >= intense_threshold_ms) return 'intense'
  if (duration_ms >= bubble_threshold_ms) return 'bubble'
  return 'normal'
}

export function createIdleWatcher(
  rawOpts: IdleOpts = {},
  callbacks: IdleWatcherCallbacks = {},
): IdleWatcher {
  const opts: Required<IdleOpts> = {
    low_energy_threshold_ms: safeNum(
      rawOpts.low_energy_threshold_ms,
      DEFAULTS.low_energy_threshold_ms,
      true,
    ),
    welcome_cooldown_ms: safeNum(
      rawOpts.welcome_cooldown_ms,
      DEFAULTS.welcome_cooldown_ms,
      true,
    ),
    welcome_bubble_threshold_ms: safeNum(
      rawOpts.welcome_bubble_threshold_ms,
      DEFAULTS.welcome_bubble_threshold_ms,
      true,
    ),
    welcome_intense_threshold_ms: safeNum(
      rawOpts.welcome_intense_threshold_ms,
      DEFAULTS.welcome_intense_threshold_ms,
      true,
    ),
  }

  function init(): IdleWatcherState {
    return {
      low_energy: false,
      last_activity_t: -Infinity,
      low_energy_start_t: -Infinity,
      last_welcome_t: -Infinity,
    }
  }

  function safeCallback(fn: (() => void) | undefined): void {
    if (!fn) return
    try {
      fn()
    } catch {
      /* swallow */
    }
  }

  function notifyActivity(s: IdleWatcherState, now_t: number): IdleWatcherState {
    if (!Number.isFinite(now_t)) return s

    if (s.low_energy) {
      // Returning from low_energy → wakeup transition.
      const duration_ms = Math.max(0, now_t - s.low_energy_start_t)
      const within_cooldown = now_t - s.last_welcome_t < opts.welcome_cooldown_ms
      const intensity = pickWelcomeIntensity(
        duration_ms,
        opts.welcome_bubble_threshold_ms,
        opts.welcome_intense_threshold_ms,
      )
      const next: IdleWatcherState = {
        low_energy: false,
        last_activity_t: now_t,
        low_energy_start_t: -Infinity,
        last_welcome_t: within_cooldown ? s.last_welcome_t : now_t,
      }
      if (!within_cooldown) {
        safeCallback(() => callbacks.onWakeup?.(now_t, duration_ms, intensity))
      }
      return next
    }

    // Already active — just record latest activity.
    if (s.last_activity_t === now_t) return s
    return { ...s, last_activity_t: now_t }
  }

  function tick(s: IdleWatcherState, now_t: number): IdleWatcherState {
    if (!Number.isFinite(now_t)) return s
    if (s.low_energy) return s
    if (!Number.isFinite(s.last_activity_t)) return s
    if (now_t - s.last_activity_t < opts.low_energy_threshold_ms) return s
    // Transition into low_energy.
    safeCallback(() => callbacks.onLowEnergy?.(now_t))
    return {
      ...s,
      low_energy: true,
      low_energy_start_t: now_t,
    }
  }

  return { init, notifyActivity, tick }
}
