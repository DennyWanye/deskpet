// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * saccadeScheduler.ts — TDD §2.3.
 *
 * Micro-saccade scheduler. Every `Uniform(min_interval_ms, max_interval_ms)`
 * the eyeballs dart by a random offset in [-amplitude, +amplitude] for
 * `duration_ms` ms, then return to zero. Offset is reported as an ADD
 * delta to ParamEyeBallX/Y — the overlay applies it on top of FR-4 gaze
 * tracking output (TDD §3.6 step 4).
 *
 * Pure: time + rng are injected. State is a tiny struct; tests can
 * snapshot it.
 */
export interface SaccadeOpts {
  min_interval_ms?: number
  max_interval_ms?: number
  duration_ms?: number
  /** Max offset in EyeBall normalised units. Default 0.04 (= 4% of ±1). */
  amplitude?: number
  rng?: () => number
}

export interface SaccadeState {
  /** Absolute timestamp the current saccade starts. */
  next_start_t: number
  /** Sampled offset for the in-progress saccade. */
  offset_x: number
  offset_y: number
  /** Absolute timestamp of the current saccade start (after triggered). */
  active_start_t: number
  /** True between active_start_t and active_start_t + duration_ms. */
  active: boolean
}

export interface SaccadeScheduler {
  init(now_t: number): SaccadeState
  tick(
    state: SaccadeState,
    now_t: number,
  ): { state: SaccadeState; offset_x: number; offset_y: number }
}

function uniform(rng: () => number, lo: number, hi: number): number {
  return lo + rng() * (hi - lo)
}

export function createSaccadeScheduler(opts: SaccadeOpts = {}): SaccadeScheduler {
  const min_interval_ms = opts.min_interval_ms ?? 500
  const max_interval_ms = opts.max_interval_ms ?? 2000
  const duration_ms = opts.duration_ms ?? 45
  const amplitude = opts.amplitude ?? 0.04
  const rng = opts.rng ?? Math.random

  function scheduleNext(now_t: number): {
    next_start_t: number
    offset_x: number
    offset_y: number
  } {
    return {
      next_start_t: now_t + uniform(rng, min_interval_ms, max_interval_ms),
      offset_x: uniform(rng, -amplitude, amplitude),
      offset_y: uniform(rng, -amplitude, amplitude),
    }
  }

  return {
    init(now_t: number): SaccadeState {
      const s = scheduleNext(now_t)
      return {
        ...s,
        active_start_t: 0,
        active: false,
      }
    },

    tick(state: SaccadeState, now_t: number) {
      let next: SaccadeState = state

      // Triggered: about to start.
      if (!next.active && now_t >= next.next_start_t) {
        next = {
          ...next,
          active: true,
          active_start_t: now_t,
        }
      }

      // End of active arc: snap back, schedule the next.
      if (next.active && now_t - next.active_start_t >= duration_ms) {
        const s = scheduleNext(now_t)
        next = {
          ...s,
          active_start_t: 0,
          active: false,
        }
      }

      const offset_x = next.active ? next.offset_x : 0
      const offset_y = next.active ? next.offset_y : 0
      return { state: next, offset_x, offset_y }
    },
  }
}
