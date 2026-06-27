// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Unit tests for visemeLipsync — B3 main path (TDD §4.4).
 */
import { describe, it, expect } from 'vitest'
import { createVisemeLipsync, VISEME_MAP } from '../visemeLipsync'

describe('visemeLipsync — B3 main path', () => {
  it('TC-B3-01 empty queue → silent', () => {
    const lip = createVisemeLipsync()
    const r = lip.sample(0)
    expect(r.v).toBe('silent')
    expect(r.mouthY).toBe(0)
    expect(r.mouthForm).toBe(0)
  })

  it('TC-B3-02 single A frame → A values', () => {
    const lip = createVisemeLipsync()
    lip.push({ v: 'A', t_ms: 100 })
    const r = lip.sample(150)
    expect(r.v).toBe('A')
    expect(r.mouthY).toBeCloseTo(VISEME_MAP.A.mouthY, 4)
    expect(r.mouthForm).toBeCloseTo(VISEME_MAP.A.mouthForm, 4)
  })

  it('TC-B3-03 before-first-frame → silent', () => {
    const lip = createVisemeLipsync()
    lip.push({ v: 'A', t_ms: 100 })
    const r = lip.sample(50)
    expect(r.v).toBe('silent')
  })

  it('TC-B3-04 blend between consecutive frames inside blend_ms window', () => {
    const lip = createVisemeLipsync({ blend_ms: 60 })
    lip.push({ v: 'A', t_ms: 0 })
    lip.push({ v: 'I', t_ms: 200 })
    // At t=170 (30ms before next), we're 50% through the 60ms blend window.
    const r = lip.sample(170)
    // 50% blend from A.mouthY=0.7 toward I.mouthY=0.2 → 0.45
    expect(r.mouthY).toBeCloseTo(0.45, 3)
    // Reports the current frame's v while blending toward next.
    expect(r.v).toBe('A')
  })

  it('TC-B3-05 stale single frame past silent_after_ms → silent fallback', () => {
    const lip = createVisemeLipsync({ silent_after_ms: 300 })
    lip.push({ v: 'A', t_ms: 100 })
    const r = lip.sample(500) // 400ms after frame, no next
    expect(r.v).toBe('silent')
  })

  it('TC-B3-06 flush clears queue', () => {
    const lip = createVisemeLipsync()
    lip.push({ v: 'O', t_ms: 0 })
    lip.flush()
    expect(lip.debug().queue_size).toBe(0)
    expect(lip.sample(0).v).toBe('silent')
  })

  it('TC-B3-07 pushMany preserves order', () => {
    const lip = createVisemeLipsync()
    lip.pushMany([
      { v: 'A', t_ms: 0 },
      { v: 'I', t_ms: 100 },
      { v: 'silent', t_ms: 200 },
    ])
    expect(lip.sample(50).v).toBe('A')
    expect(lip.sample(150).v).toBe('I')
    expect(lip.sample(250).v).toBe('silent')
  })

  it('TC-B3-08 out-of-order push sorts correctly', () => {
    const lip = createVisemeLipsync({ blend_ms: 0 })
    lip.push({ v: 'I', t_ms: 200 })
    lip.push({ v: 'A', t_ms: 100 })
    expect(lip.sample(150).v).toBe('A')
    expect(lip.sample(250).v).toBe('I')
  })

  it('TC-B3-09 invalid frame is silently rejected', () => {
    const lip = createVisemeLipsync()
    lip.push({ v: 'A', t_ms: Number.NaN } as never)
    lip.push({ v: 'BOGUS' as never, t_ms: 0 })
    expect(lip.debug().queue_size).toBe(0)
  })
})
