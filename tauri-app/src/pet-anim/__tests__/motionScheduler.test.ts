/**
 * motionScheduler.test.ts — TDD §4.6 (TC-MS-01..05)
 */
import { describe, expect, it } from 'vitest'
import { createMotionScheduler } from '../motionScheduler'
import { fakeRng } from './_helpers'

describe('motionScheduler', () => {
  it('TC-MS-01 (P0) shouldSwitch 在 period * (1 - jitter) 之前永远 false', () => {
    const sched = createMotionScheduler({
      switch_period_ms: 1000,
      jitter_ratio: 0.3,
      rng: fakeRng(1),
    })
    const state = sched.init(0)
    // Minimum possible next is 700 ms. Anything before that must be false.
    expect(sched.shouldSwitch(state, 699)).toBe(false)
  })

  it('TC-MS-02 (P0) shouldSwitch 在 period * (1 + jitter) 之后必为 true', () => {
    const sched = createMotionScheduler({
      switch_period_ms: 1000,
      jitter_ratio: 0.3,
      rng: fakeRng(2),
    })
    const state = sched.init(0)
    // Maximum possible next is 1300 ms. Anything after must be true.
    expect(sched.shouldSwitch(state, 1301)).toBe(true)
  })

  it('TC-MS-03 (P0) forceSwitchNow 后 shouldSwitch === true', () => {
    const sched = createMotionScheduler({
      switch_period_ms: 5000,
      rng: fakeRng(3),
    })
    let state = sched.init(0)
    state = sched.forceSwitchNow(state, 500).state
    expect(sched.shouldSwitch(state, 500)).toBe(true)
  })

  it('TC-MS-04 (P0) scheduleNext 之后 next_switch_t 推进且带 jitter', () => {
    const sched = createMotionScheduler({
      switch_period_ms: 1000,
      jitter_ratio: 0.3,
      rng: fakeRng(4),
    })
    let state = sched.init(0)
    const first = state.next_switch_t
    state = sched.scheduleNext(state, 1000).state
    expect(state.next_switch_t).toBeGreaterThan(1000)
    expect(state.next_switch_t).not.toEqual(first)
  })

  it('TC-MS-05 (P1) seeded rng 可复现 jitter 序列', () => {
    const trace = () => {
      const sched = createMotionScheduler({
        switch_period_ms: 1000,
        rng: fakeRng(99),
      })
      let state = sched.init(0)
      const ts: number[] = [state.next_switch_t]
      for (let i = 0; i < 10; i++) {
        state = sched.scheduleNext(state, ts[ts.length - 1]).state
        ts.push(state.next_switch_t)
      }
      return ts
    }
    expect(trace()).toEqual(trace())
  })
})
