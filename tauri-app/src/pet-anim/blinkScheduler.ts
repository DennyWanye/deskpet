// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * blinkScheduler.ts — TDD §2.2 / §3.2 / §3.3.
 *
 * Generates a sequence of blink events with intervals drawn from a
 * log-normal distribution so the result is perceived as "natural"
 * (humans blink with heavy right tail). Closed-eye phase is a half-sine
 * over `close_duration_ms`. Occasional double-blinks (10% by default)
 * schedule a follow-up blink ~200 ms after the first.
 *
 * Distribution (PRD FR-2, v2 mu correction):
 *   sigma = 0.4
 *   mu = ln(1/blink_hz) - sigma²/2          // E[X] = 1/blink_hz exactly
 *   interval_ms = 1000 * exp(mu + sigma * Z)
 * where Z ~ N(0,1) via Box-Muller from the injected rng.
 *
 * The module is pure: time and randomness are injected so unit tests are
 * deterministic. State is opaque to the caller — `init` returns it,
 * `tick` returns the next state alongside the current eye-open multiplier.
 *
 * Write semantics (FR-2): the multiplier is meant to be MULTIPLIED into
 * ParamEyeLOpen / ParamEyeROpen by the overlay (so motion3's own
 * blinks are preserved as a baseline). When blink_hz=0 the multiplier
 * is always 1.0 → caller can skip the multiply entirely.
 */
export interface BlinkOpts {
  /** Mean blink frequency in Hz. 0 disables — multiplier ≡ 1. */
  blink_hz: number
  /** Injectable rng for tests; default Math.random. */
  rng?: () => number
  /** Closed-eye phase length. Default 100 ms. */
  close_duration_ms?: number
  /** Probability of triggering a follow-up blink ~200 ms after the first. */
  double_blink_prob?: number
  /** Lognormal sigma. Default 0.4 (≈ matches human blink IQR). */
  sigma?: number
}

export interface BlinkState {
  /** Absolute DOMHighResTimeStamp of the next scheduled blink start. */
  next_blink_t: number
  /** True while the eye-close arc is active. */
  in_blink: boolean
  /** Absolute timestamp when the current close arc started. */
  blink_start_t: number
  /** True if the *next* blink is the "follow-up half" of a double blink. */
  pending_double: boolean
}

export interface BlinkScheduler {
  init(now_t: number): BlinkState
  tick(state: BlinkState, now_t: number): { state: BlinkState; eye_open_multiplier: number }
}

/**
 * Box-Muller normal sample. We always consume exactly 2 rng calls per
 * blink interval so determinism is preserved across re-seedings.
 */
function boxMuller(rng: () => number): number {
  const u1 = Math.max(rng(), 1e-12)
  const u2 = rng()
  return Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2)
}

function nextInterval(rng: () => number, blink_hz: number, sigma: number): number {
  const mu = Math.log(1 / blink_hz) - (sigma * sigma) / 2
  const z = boxMuller(rng)
  // exp(mu + sigma*z) is in *seconds*; convert to ms.
  return 1000 * Math.exp(mu + sigma * z)
}

export function createBlinkScheduler(opts: BlinkOpts): BlinkScheduler {
  const rng = opts.rng ?? Math.random
  const close_duration_ms = opts.close_duration_ms ?? 100
  const double_blink_prob = opts.double_blink_prob ?? 0.1
  const sigma = opts.sigma ?? 0.4

  return {
    init(now_t: number): BlinkState {
      // When blink_hz is 0 we still construct a state but `tick` will
      // short-circuit before sampling, so the next_blink_t value is moot.
      const first_offset =
        opts.blink_hz > 0 ? nextInterval(rng, opts.blink_hz, sigma) : Infinity
      return {
        next_blink_t: now_t + first_offset,
        in_blink: false,
        blink_start_t: 0,
        pending_double: false,
      }
    },

    tick(state: BlinkState, now_t: number) {
      // Frequency-zero short circuit: never blink, never sample.
      if (opts.blink_hz <= 0) {
        return { state, eye_open_multiplier: 1 }
      }

      // Defensive: if the clock went backwards (HMR, tab focus quirk),
      // don't punish the user with a flurry of catch-up blinks.
      if (now_t < state.blink_start_t || now_t < state.next_blink_t - 60_000) {
        return { state, eye_open_multiplier: state.in_blink ? 0 : 1 }
      }

      let next: BlinkState = state

      // ───────── transition: idle → in_blink ─────────
      if (!next.in_blink && now_t >= next.next_blink_t) {
        next = {
          ...next,
          in_blink: true,
          blink_start_t: now_t,
        }
      }

      // ───────── transition: in_blink → idle ─────────
      if (next.in_blink && now_t - next.blink_start_t >= close_duration_ms) {
        let pending_double = false
        let nextInterval_ms: number

        if (next.pending_double) {
          // We just finished the follow-up of a double-blink; schedule
          // a fresh long interval and reset the flag.
          pending_double = false
          nextInterval_ms = nextInterval(rng, opts.blink_hz, sigma)
        } else {
          // Decide whether this blink becomes a double.
          const roll = rng()
          if (roll < double_blink_prob) {
            pending_double = true
            nextInterval_ms = 200 // short fixed gap for the follow-up
          } else {
            nextInterval_ms = nextInterval(rng, opts.blink_hz, sigma)
          }
        }

        next = {
          next_blink_t: now_t + nextInterval_ms,
          in_blink: false,
          blink_start_t: 0,
          pending_double,
        }
      }

      // ───────── compute multiplier ─────────
      let eye_open_multiplier: number
      if (next.in_blink) {
        // Half-sine eye-close arc — 0 at start, 1 at midpoint, 0 at end.
        // We invert so multiplier is 1 → 0 → 1 (eyes open → closed → open).
        const phase = (now_t - next.blink_start_t) / close_duration_ms
        const eased = Math.sin(Math.min(1, Math.max(0, phase)) * Math.PI)
        eye_open_multiplier = 1 - eased
      } else {
        eye_open_multiplier = 1
      }

      return { state: next, eye_open_multiplier }
    },
  }
}
