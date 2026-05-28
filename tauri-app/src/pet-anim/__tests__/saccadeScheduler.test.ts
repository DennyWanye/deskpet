// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * saccadeScheduler.test.ts — TDD §4.3 (TC-S-01..04)
 */
import { describe, expect, it } from 'vitest'
import { createSaccadeScheduler } from '../saccadeScheduler'
import { fakeRng } from './_helpers'

describe('saccadeScheduler', () => {
  it('TC-S-01 (P0) 30s 模拟触发次数 ∈ [15, 60]', () => {
    const sched = createSaccadeScheduler({ rng: fakeRng(7) })
    let state = sched.init(0)
    let triggers = 0
    let was_active = false
    for (let t = 0; t <= 30_000; t += 10) {
      state = sched.tick(state, t).state
      if (!was_active && state.active) triggers++
      was_active = state.active
    }
    expect(triggers).toBeGreaterThanOrEqual(15)
    expect(triggers).toBeLessThanOrEqual(60)
  })

  it('TC-S-02 (P0) 期间 |offset| ≤ amplitude', () => {
    const amplitude = 0.04
    const sched = createSaccadeScheduler({ rng: fakeRng(13), amplitude })
    let state = sched.init(0)
    for (let t = 0; t <= 60_000; t += 10) {
      const r = sched.tick(state, t)
      state = r.state
      expect(Math.abs(r.offset_x)).toBeLessThanOrEqual(amplitude + 1e-9)
      expect(Math.abs(r.offset_y)).toBeLessThanOrEqual(amplitude + 1e-9)
    }
  })

  it('TC-S-03 (P0) 结束后 offset_x = offset_y = 0', () => {
    const sched = createSaccadeScheduler({
      rng: fakeRng(21),
      min_interval_ms: 100,
      max_interval_ms: 200,
      duration_ms: 50,
    })
    let state = sched.init(0)
    // Walk through one full saccade arc.
    let was_active = false
    let arc_end_t = -1
    for (let t = 0; t < 1000; t += 1) {
      const r = sched.tick(state, t)
      state = r.state
      if (was_active && !state.active) {
        // Just after the arc ends, the immediate next tick reports 0/0.
        arc_end_t = t
        const tail = sched.tick(state, t + 1)
        expect(tail.offset_x).toBe(0)
        expect(tail.offset_y).toBe(0)
        break
      }
      was_active = state.active
    }
    expect(arc_end_t).toBeGreaterThan(0)
  })

  it('TC-S-04 (P1) seeded rng 可复现', () => {
    const trace = (seed: number) => {
      const sched = createSaccadeScheduler({ rng: fakeRng(seed) })
      let state = sched.init(0)
      const offsets: Array<[number, number]> = []
      for (let t = 0; t < 5000; t += 50) {
        const r = sched.tick(state, t)
        state = r.state
        offsets.push([r.offset_x, r.offset_y])
      }
      return offsets
    }
    expect(trace(1234)).toEqual(trace(1234))
  })
})
