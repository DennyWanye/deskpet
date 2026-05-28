// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * motionPicker.test.ts — TDD §4.5 (TC-MP-01..05)
 */
import { describe, expect, it } from 'vitest'
import { createMotionPicker } from '../motionPicker'
import { fakeRng } from './_helpers'

describe('motionPicker', () => {
  it('TC-MP-01 (P0) candidates=[1,2,3], 连 6 次 pick, 每个 idx ≥ 1 次', () => {
    const p = createMotionPicker({ rng: fakeRng(1) })
    let state = p.init()
    const picks: number[] = []
    for (let i = 0; i < 6; i++) {
      const r = p.pick(state, [1, 2, 3])
      state = r.state
      if (r.idx !== null) picks.push(r.idx)
    }
    expect(new Set(picks).has(1)).toBe(true)
    expect(new Set(picks).has(2)).toBe(true)
    expect(new Set(picks).has(3)).toBe(true)
  })

  it('TC-MP-02 (P0) candidates=[] → null', () => {
    const p = createMotionPicker({ rng: fakeRng(2) })
    const r = p.pick(p.init(), [])
    expect(r.idx).toBeNull()
  })

  it('TC-MP-03 (P0) recent_idx 最近 3 个不重复 (candidates ≥ 4)', () => {
    const p = createMotionPicker({ rng: fakeRng(3) })
    let state = p.init()
    const seq: number[] = []
    for (let i = 0; i < 12; i++) {
      const r = p.pick(state, [1, 2, 3, 4, 5])
      state = r.state
      seq.push(r.idx!)
    }
    // Every consecutive 3-window has no duplicates.
    for (let i = 0; i + 3 <= seq.length; i++) {
      const window = seq.slice(i, i + 3)
      expect(new Set(window).size).toBe(3)
    }
  })

  it('TC-MP-04 (P0) candidates=[5] → 始终返回 5', () => {
    const p = createMotionPicker({ rng: fakeRng(4) })
    let state = p.init()
    for (let i = 0; i < 20; i++) {
      const r = p.pick(state, [5])
      state = r.state
      expect(r.idx).toBe(5)
    }
  })

  it('TC-MP-05 (P1) candidates=[1,2] → 不死锁, 每次都返回 1 或 2', () => {
    const p = createMotionPicker({ rng: fakeRng(5) })
    let state = p.init()
    for (let i = 0; i < 30; i++) {
      const r = p.pick(state, [1, 2])
      state = r.state
      expect([1, 2]).toContain(r.idx)
    }
  })
})
