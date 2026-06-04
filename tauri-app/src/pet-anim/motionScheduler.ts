// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * motionScheduler.ts — TDD §2.6 (v2: clock side, paired with motionPicker).
 *
 * Tracks "when is it time to play the next motion". `shouldSwitch(now_t)`
 * fires when `now_t >= next_switch_t`; the caller is then expected to
 * `pick()` a new motion idx and call `scheduleNext(now_t)` to advance
 * the schedule. `forceSwitchNow(now_t)` is used by the App when
 * PetStateMachine.tick reports `state_changed=true` so the motion swaps
 * immediately on a supervisor severity bump (PRD FR-5 v2 M4 fix).
 */
export interface MotionSchedulerOpts {
  switch_period_ms: number
  /** Per-switch jitter on the period; default ±30%. */
  jitter_ratio?: number
  rng?: () => number
}

export interface MotionSchedulerState {
  next_switch_t: number
}

export interface MotionScheduler {
  init(now_t: number): MotionSchedulerState
  shouldSwitch(state: MotionSchedulerState, now_t: number): boolean
  scheduleNext(
    state: MotionSchedulerState,
    now_t: number,
  ): { state: MotionSchedulerState }
  forceSwitchNow(
    state: MotionSchedulerState,
    now_t: number,
  ): { state: MotionSchedulerState }
}

export function createMotionScheduler(opts: MotionSchedulerOpts): MotionScheduler {
  const jitter_ratio = opts.jitter_ratio ?? 0.3
  const rng = opts.rng ?? Math.random

  function computeNext(now_t: number): number {
    // jitter ∈ [-1, +1] × jitter_ratio
    const j = (rng() * 2 - 1) * jitter_ratio
    return now_t + Math.max(50, opts.switch_period_ms * (1 + j))
  }

  return {
    init(now_t) {
      return { next_switch_t: computeNext(now_t) }
    },
    shouldSwitch(state, now_t) {
      return now_t >= state.next_switch_t
    },
    scheduleNext(_state, now_t) {
      return { state: { next_switch_t: computeNext(now_t) } }
    },
    forceSwitchNow(_state, now_t) {
      // Force shouldSwitch === true on the very next tick.
      return { state: { next_switch_t: now_t } }
    },
  }
}
