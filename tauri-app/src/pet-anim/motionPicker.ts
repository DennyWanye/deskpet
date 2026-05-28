// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * motionPicker.ts — TDD §2.5 (v2: pure selection, no clock).
 *
 * Stateful picker that selects the next motion index from a candidate
 * pool while remembering the last 3 picks so we don't repeat them when
 * possible. Designed to be paired with `motionScheduler` (clock side)
 * — together they implement FR-5's "round-robin with anti-repeat".
 *
 * Edge cases:
 *   - candidates.length === 0 → returns null (caller decides what to do —
 *     usually keep the current motion).
 *   - candidates.length === 1 → always returns that idx; clears recent_idx
 *     so subsequent larger pools aren't permanently filtered against the
 *     stale single.
 *   - all candidates are in recent_idx → fall back to the oldest entry
 *     in recent_idx and shift it out.
 */
export type MotionTag = 'fast' | 'medium' | 'slow' | 'special'

export interface MotionPickerOpts {
  rng?: () => number
}

export interface MotionPickerState {
  recent_idx: number[]
}

export interface MotionPicker {
  init(): MotionPickerState
  pick(
    state: MotionPickerState,
    candidates: number[],
  ): { state: MotionPickerState; idx: number | null }
}

export function createMotionPicker(opts: MotionPickerOpts = {}): MotionPicker {
  const rng = opts.rng ?? Math.random

  return {
    init() {
      return { recent_idx: [] }
    },

    pick(state, candidates) {
      if (candidates.length === 0) return { state, idx: null }
      if (candidates.length === 1) {
        return {
          state: { recent_idx: [] },
          idx: candidates[0],
        }
      }
      const recent = state.recent_idx.slice(-3)
      const available = candidates.filter((c) => !recent.includes(c))
      let chosen: number
      if (available.length > 0) {
        const r = Math.floor(rng() * available.length)
        chosen = available[Math.min(r, available.length - 1)]
      } else {
        // All recent — fall back to oldest in recent (which is the least
        // recently chosen and so most "ready" for replay).
        chosen = recent[0]
      }
      const next_recent = [...state.recent_idx, chosen].slice(-3)
      return { state: { recent_idx: next_recent }, idx: chosen }
    },
  }
}
