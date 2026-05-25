/**
 * mouthFader (pet-anim/B4) — Pet Animation UX v2.
 *
 * Smoothly fades ParamMouthOpenY from a starting value to 0 over `fade_ms` with
 * an ease-out cubic curve. Also supports an 800ms silence-timeout fallback
 * (M-4 round-1 evidence) when the backend forgets to emit `tts_end`.
 *
 * State machine
 *   idle       — sample() returns null (caller leaves mouth at whatever else
 *                wrote ParamMouthOpenY, e.g. B3 viseme)
 *   pending    — startWithTimeout armed but fade not yet running; sample
 *                returns null until silence_timeout elapses
 *   fading     — fade window active; sample returns the eased value
 *
 * The caller cancels pending/fading on any new viseme arrival (B3 takes over
 * the mouth) to avoid one-frame snap-back.
 *
 * NFR-6: every now_t is DOMHighResTimeStamp passed by the caller.
 */

export interface MouthFaderOpts {
  /** Fade duration in ms. PRD §3 B4: 150-300, default 200. */
  fade_ms?: number
}

export interface MouthFader {
  /** Start a fade right now from `from` → 0 over `fade_ms`. */
  start(from: number, duration_ms: number, now_t: number): void
  /**
   * Arm the silence-timeout: if no `cancel()` is called within
   * `silence_timeout_ms`, automatically launches a fade from the last value
   * recorded via `noteCurrentMouth`. PRD §3 B4 M-4: default 800.
   */
  startWithTimeout(silence_timeout_ms: number, now_t: number): void
  /** Cancel any pending timeout or in-flight fade (e.g. new viseme arrived). */
  cancel(): void
  /**
   * Per-frame value query.
   * - idle              → null
   * - pending timeout   → null (mouth held by previous writer)
   * - fading            → number in [0, from]
   * - completed         → 0 for the final frame then auto-clears to null next call.
   */
  sample(now_t: number): number | null
  /**
   * Caller records the last visible mouth value so a subsequent
   * `startWithTimeout` knows what value to start the fade from when the
   * silence timeout fires.
   */
  noteCurrentMouth(v: number): void
  /** Debug snapshot for tests / probes. */
  debug(): {
    mode: 'idle' | 'pending' | 'fading'
    from: number
    duration_ms: number
    start_t: number
    timeout_at: number
  }
}

const DEFAULTS: Required<MouthFaderOpts> = {
  fade_ms: 200,
}

function safeNum(v: number | undefined, fallback: number, mustBePositive = false): number {
  if (v === undefined || !Number.isFinite(v)) return fallback
  if (mustBePositive && v <= 0) return fallback
  return v
}

function easeOutCubic(t01: number): number {
  const x = Math.max(0, Math.min(1, t01))
  const inv = 1 - x
  return 1 - inv * inv * inv
}

export function createMouthFader(rawOpts: MouthFaderOpts = {}): MouthFader {
  const defaultFadeMs = safeNum(rawOpts.fade_ms, DEFAULTS.fade_ms, true)

  type Mode = 'idle' | 'pending' | 'fading'
  let mode: Mode = 'idle'
  let from = 0
  let duration_ms = defaultFadeMs
  let start_t = -Infinity
  let timeout_at = -Infinity
  let last_observed_mouth = 0

  function start(fromVal: number, dur: number, now_t: number): void {
    if (!Number.isFinite(fromVal) || !Number.isFinite(now_t)) return
    mode = 'fading'
    from = Math.max(0, Math.min(1, fromVal))
    duration_ms = safeNum(dur, defaultFadeMs, true)
    start_t = now_t
    timeout_at = -Infinity
    last_observed_mouth = from
  }

  function startWithTimeout(silence_timeout_ms: number, now_t: number): void {
    if (!Number.isFinite(now_t)) return
    const tmo = safeNum(silence_timeout_ms, 800, true)
    mode = 'pending'
    timeout_at = now_t + tmo
    start_t = -Infinity
    from = last_observed_mouth
    duration_ms = defaultFadeMs
  }

  function cancel(): void {
    mode = 'idle'
    from = 0
    start_t = -Infinity
    timeout_at = -Infinity
    duration_ms = defaultFadeMs
  }

  function sample(now_t: number): number | null {
    if (!Number.isFinite(now_t)) return null
    if (mode === 'idle') return null

    if (mode === 'pending') {
      if (now_t < timeout_at) return null
      // Timeout elapsed — promote to fading using last observed mouth.
      mode = 'fading'
      from = last_observed_mouth
      start_t = now_t
      duration_ms = defaultFadeMs
      timeout_at = -Infinity
      // fall through to fading branch
    }

    if (mode === 'fading') {
      const dt = now_t - start_t
      if (dt >= duration_ms) {
        // Final frame at 0; next call goes back to idle.
        mode = 'idle'
        return 0
      }
      const t01 = dt / duration_ms
      const eased = easeOutCubic(t01)
      return from * (1 - eased)
    }

    return null
  }

  function noteCurrentMouth(v: number): void {
    if (!Number.isFinite(v)) return
    last_observed_mouth = Math.max(0, Math.min(1, v))
  }

  function debug(): {
    mode: 'idle' | 'pending' | 'fading'
    from: number
    duration_ms: number
    start_t: number
    timeout_at: number
  } {
    return { mode, from, duration_ms, start_t, timeout_at }
  }

  return { start, startWithTimeout, cancel, sample, noteCurrentMouth, debug }
}
