/**
 * Unit tests for mouthFader — B4 (TDD §4.5).
 *
 * Cases:
 *   TC-B4-01  sample idle → null
 *   TC-B4-02  start(0.6, 200, t0) → eased fade from 0.6 → 0 over 200ms
 *   TC-B4-03  cancel mid-fade → subsequent sample returns null
 *   TC-B4-04  startWithTimeout(800, t0) → null for 800ms then auto-fade
 *   TC-B4-05  fade completes → final frame is exactly 0, next sample null
 *   TC-B4-06  noteCurrentMouth supplies origin for startWithTimeout
 *   TC-B4-07  Invalid inputs no-op safely
 */
import { describe, it, expect } from 'vitest'
import { createMouthFader } from '../mouthFader'

describe('mouthFader — B4', () => {
  it('TC-B4-01 idle sample returns null', () => {
    const f = createMouthFader()
    expect(f.sample(0)).toBe(null)
    expect(f.sample(1000)).toBe(null)
  })

  it('TC-B4-02 start fade follows ease-out cubic from `from` → 0', () => {
    const f = createMouthFader()
    f.start(0.6, 200, 0)
    // t=0 → eased=0 → value = 0.6 * 1 = 0.6
    expect(f.sample(0)).toBeCloseTo(0.6, 6)
    // t=100 (50%) → easeOutCubic(0.5) = 1 - 0.5^3 = 0.875 → remaining = 0.125 → 0.075
    expect(f.sample(100)).toBeCloseTo(0.6 * 0.125, 6)
    // t=200 (100%) → completion frame = 0
    expect(f.sample(200)).toBe(0)
  })

  it('TC-B4-03 cancel mid-fade → null afterwards', () => {
    const f = createMouthFader()
    f.start(0.5, 200, 0)
    expect(f.sample(50)).toBeGreaterThan(0)
    f.cancel()
    expect(f.sample(60)).toBe(null)
    expect(f.debug().mode).toBe('idle')
  })

  it('TC-B4-04 startWithTimeout: null until silence_timeout, then auto-fade (M-4)', () => {
    const f = createMouthFader()
    f.noteCurrentMouth(0.4)
    f.startWithTimeout(800, 0)
    expect(f.debug().mode).toBe('pending')
    expect(f.sample(0)).toBe(null)
    expect(f.sample(799)).toBe(null)
    // 800ms exactly: promote to fading with from=0.4
    const v = f.sample(800)
    expect(v).not.toBe(null)
    expect(v!).toBeCloseTo(0.4, 6)
    expect(f.debug().mode).toBe('fading')
  })

  it('TC-B4-05 fade completes and clears to idle', () => {
    const f = createMouthFader()
    f.start(0.3, 200, 1000)
    expect(f.sample(1199)).toBeGreaterThan(0)
    expect(f.sample(1200)).toBe(0)
    // After completion frame, mode goes back to idle.
    expect(f.sample(1201)).toBe(null)
    expect(f.debug().mode).toBe('idle')
  })

  it('TC-B4-06 noteCurrentMouth supplies origin for later timeout fade', () => {
    const f = createMouthFader()
    f.noteCurrentMouth(0.7)
    f.noteCurrentMouth(0.9) // last write wins
    f.startWithTimeout(100, 0)
    expect(f.sample(100)).toBeCloseTo(0.9, 6)
  })

  it('TC-B4-07 invalid inputs no-op', () => {
    const f = createMouthFader()
    f.start(Number.NaN, 200, 0)
    expect(f.sample(50)).toBe(null)
    f.noteCurrentMouth(Number.NaN)
    f.startWithTimeout(Number.NaN, Number.NaN)
    expect(f.sample(2000)).toBe(null)
  })

  it('TC-B4-08 new start() interrupts a pending timeout cleanly', () => {
    const f = createMouthFader()
    f.noteCurrentMouth(0.5)
    f.startWithTimeout(800, 0)
    expect(f.debug().mode).toBe('pending')
    // viseme came back, then ended cleanly → caller calls start() directly
    f.start(0.3, 100, 100)
    expect(f.debug().mode).toBe('fading')
    expect(f.sample(100)).toBeCloseTo(0.3, 6)
    expect(f.sample(200)).toBe(0)
  })
})
