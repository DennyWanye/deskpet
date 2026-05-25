/**
 * Unit tests for heldStateMachine — A1 (TDD §4.1).
 *
 * Cases:
 *   TC-A1-01  init → idle, tick is no-op
 *   TC-A1-02  onDragStart transitions to being_held with surprise=1, wobble=0 at t=0
 *   TC-A1-03  Wobble peaks near 1/4 period within being_held (sin(π/2)≈1)
 *   TC-A1-04  Wobble amplitude decays by exp(-1) at t = decay_const
 *   TC-A1-05  surprise_factor linearly decays to 0 over surprise_duration_ms
 *   TC-A1-06  onDragEnd → spring_back; after spring_back_ms returns to idle
 *   TC-A1-07  Spring-back monotonically decreasing magnitude (ease-out cubic)
 *   TC-A1-08  Non-finite now_t is silently no-op (NFR-6 defence)
 */
import { describe, it, expect } from 'vitest'
import { createDragStateMachine } from '../heldStateMachine'

describe('heldStateMachine — A1', () => {
  it('TC-A1-01 init() returns idle context, tick is no-op', () => {
    const fsm = createDragStateMachine()
    const ctx = fsm.init()
    expect(ctx.state).toBe('idle')
    expect(ctx.held_start_t).toBe(-Infinity)

    const step = fsm.tick(ctx, 12345)
    expect(step.wobble_delta).toBe(0)
    expect(step.surprise_factor).toBe(0)
    expect(step.ctx.state).toBe('idle')
  })

  it('TC-A1-02 onDragStart → being_held with surprise=1 and wobble=0 at t=0', () => {
    const fsm = createDragStateMachine()
    const r = fsm.onDragStart(fsm.init(), 1000)
    expect(r.ctx.state).toBe('being_held')
    expect(r.ctx.held_start_t).toBe(1000)
    expect(r.wobble_delta).toBe(0) // sin(0) = 0
    expect(r.surprise_factor).toBe(1)
  })

  it('TC-A1-03 wobble peaks near 1/4 period within being_held', () => {
    const fsm = createDragStateMachine({
      wobble_amplitude_deg: 8,
      wobble_period_ms: 300,
      wobble_decay_const_ms: 4000,
    })
    const start = fsm.onDragStart(fsm.init(), 0)
    // t = period/4 = 75ms → sin(π/2) = 1 → wobble = 8 × 1 × exp(-75/4000) ≈ 7.852
    const peak = fsm.tick(start.ctx, 75)
    expect(peak.wobble_delta).toBeCloseTo(8 * Math.exp(-75 / 4000), 4)
    expect(peak.wobble_delta).toBeGreaterThan(7.8)
    expect(peak.wobble_delta).toBeLessThan(8)
  })

  it('TC-A1-04 wobble envelope decays by exp(-1) at t = decay_const', () => {
    const decay = 4000
    const fsm = createDragStateMachine({
      wobble_amplitude_deg: 8,
      wobble_period_ms: 300,
      wobble_decay_const_ms: decay,
    })
    const start = fsm.onDragStart(fsm.init(), 0)
    // At t = decay envelope |wobble| ≤ amplitude × exp(-1)
    const envelope_cap = 8 * Math.exp(-1)
    for (const t of [decay, decay + 75, decay + 150, decay + 225, decay + 300]) {
      const v = fsm.tick(start.ctx, t).wobble_delta
      expect(Math.abs(v)).toBeLessThanOrEqual(envelope_cap + 1e-6)
    }
  })

  it('TC-A1-05 surprise_factor linearly decays over surprise_duration_ms', () => {
    const fsm = createDragStateMachine({ surprise_duration_ms: 200 })
    const start = fsm.onDragStart(fsm.init(), 0)
    expect(fsm.tick(start.ctx, 0).surprise_factor).toBe(1)
    expect(fsm.tick(start.ctx, 100).surprise_factor).toBeCloseTo(0.5, 4)
    expect(fsm.tick(start.ctx, 200).surprise_factor).toBe(0)
    expect(fsm.tick(start.ctx, 500).surprise_factor).toBe(0)
  })

  it('TC-A1-06 onDragEnd → spring_back → idle after spring_back_ms', () => {
    const fsm = createDragStateMachine({ spring_back_ms: 250 })
    const start = fsm.onDragStart(fsm.init(), 0)
    // capture wobble mid-hold then end the drag
    const midWobble = fsm.tick(start.ctx, 75).wobble_delta
    const end = fsm.onDragEnd(start.ctx, 75)
    expect(end.ctx.state).toBe('spring_back')
    expect(end.ctx.release_t).toBe(75)
    expect(end.ctx.release_wobble_deg).toBeCloseTo(midWobble, 6)

    // mid-spring still non-zero
    const mid = fsm.tick(end.ctx, 75 + 125)
    expect(mid.ctx.state).toBe('spring_back')
    expect(Math.abs(mid.wobble_delta)).toBeLessThan(Math.abs(midWobble))

    // at completion: state returns to idle and wobble = 0
    const post = fsm.tick(end.ctx, 75 + 250)
    expect(post.ctx.state).toBe('idle')
    expect(post.wobble_delta).toBe(0)
    expect(post.surprise_factor).toBe(0)
  })

  it('TC-A1-07 spring_back magnitude monotonically decreasing', () => {
    const fsm = createDragStateMachine({ spring_back_ms: 250 })
    const start = fsm.onDragStart(fsm.init(), 0)
    const end = fsm.onDragEnd(start.ctx, 75)
    const samples = [0, 62.5, 125, 187.5, 240]
    const magnitudes = samples.map((dt) => Math.abs(fsm.tick(end.ctx, 75 + dt).wobble_delta))
    for (let i = 1; i < magnitudes.length; i++) {
      expect(magnitudes[i]).toBeLessThanOrEqual(magnitudes[i - 1] + 1e-9)
    }
    expect(magnitudes[0]).toBeGreaterThan(0)
    expect(magnitudes[magnitudes.length - 1]).toBeLessThan(magnitudes[0])
  })

  it('TC-A1-08 non-finite now_t / invalid opts → safe no-op', () => {
    const fsm = createDragStateMachine({
      wobble_amplitude_deg: Number.NaN, // falls back to default 8
      wobble_period_ms: -100, // falls back to default 300
      wobble_decay_const_ms: 0, // falls back to default 4000
      spring_back_ms: Infinity, // falls back to default 250
    })
    // bad now_t → emptyStep(ctx) with no state change
    const r1 = fsm.tick(fsm.init(), Number.NaN)
    expect(r1.wobble_delta).toBe(0)
    expect(r1.surprise_factor).toBe(0)
    expect(r1.ctx.state).toBe('idle')

    const r2 = fsm.onDragStart(fsm.init(), Number.POSITIVE_INFINITY)
    expect(r2.ctx.state).toBe('idle')
  })

  it('TC-A1-09 onDragEnd without prior start is idempotent no-op', () => {
    const fsm = createDragStateMachine()
    const r = fsm.onDragEnd(fsm.init(), 100)
    expect(r.ctx.state).toBe('idle')
    expect(r.wobble_delta).toBe(0)
    expect(r.surprise_factor).toBe(0)
  })
})
