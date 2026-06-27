// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Unit tests for thinkingObserver — B2 (TDD §4.3).
 *
 * Cases:
 *   TC-B2-01  notifyStart → active=true; onChange fires (true, t)
 *   TC-B2-02  notifyEnd → active=false; onChange fires (false, t)
 *   TC-B2-03  Stale notifyEnd (no prior start) is a silent no-op
 *   TC-B2-04  tick past max_duration_ms → active forced to false
 *   TC-B2-05  notifyFirstChunk → active=false immediately (M-1)
 *   TC-B2-06  Non-finite now_t is a safe no-op
 *   TC-B2-07  notifyStart while already active resets start_t
 */
import { describe, it, expect, vi } from 'vitest'
import { createThinkingObserver } from '../thinkingObserver'

describe('thinkingObserver — B2', () => {
  it('TC-B2-01 notifyStart → active=true', () => {
    const onChange = vi.fn()
    const obs = createThinkingObserver({}, onChange)
    obs.notifyStart(100)
    expect(obs.isActive(100)).toBe(true)
    expect(onChange).toHaveBeenCalledWith(true, 100)
  })

  it('TC-B2-02 notifyEnd → active=false', () => {
    const onChange = vi.fn()
    const obs = createThinkingObserver({}, onChange)
    obs.notifyStart(100)
    obs.notifyEnd(200)
    expect(obs.isActive(200)).toBe(false)
    expect(onChange).toHaveBeenLastCalledWith(false, 200)
  })

  it('TC-B2-03 stale notifyEnd is no-op', () => {
    const onChange = vi.fn()
    const obs = createThinkingObserver({}, onChange)
    obs.notifyEnd(50) // no prior start
    expect(obs.isActive(50)).toBe(false)
    expect(onChange).not.toHaveBeenCalled()
  })

  it('TC-B2-04 tick past max_duration_ms forces active=false', () => {
    const onChange = vi.fn()
    const obs = createThinkingObserver({ max_duration_ms: 90_000 }, onChange)
    obs.notifyStart(0)
    expect(obs.tick(89_999)).toBe(true)
    expect(obs.tick(90_000)).toBe(false)
    expect(onChange).toHaveBeenLastCalledWith(false, 90_000)
  })

  it('TC-B2-05 notifyFirstChunk → active=false (M-1)', () => {
    const onChange = vi.fn()
    const obs = createThinkingObserver({}, onChange)
    obs.notifyStart(100)
    expect(obs.isActive(100)).toBe(true)
    obs.notifyFirstChunk(150)
    expect(obs.isActive(150)).toBe(false)
    // Fires both transitions: true at 100, false at 150
    expect(onChange).toHaveBeenCalledTimes(2)
    expect(onChange).toHaveBeenLastCalledWith(false, 150)
  })

  it('TC-B2-06 non-finite now_t is a no-op', () => {
    const onChange = vi.fn()
    const obs = createThinkingObserver({}, onChange)
    obs.notifyStart(Number.NaN)
    expect(obs.isActive(0)).toBe(false)
    obs.notifyStart(100)
    obs.notifyEnd(Number.NaN)
    expect(obs.isActive(100)).toBe(true) // unchanged
  })

  it('TC-B2-07 notifyStart while active resets timer', () => {
    const obs = createThinkingObserver({ max_duration_ms: 90_000 })
    obs.notifyStart(0)
    obs.notifyStart(50_000) // reset baseline
    expect(obs.tick(50_000 + 89_999)).toBe(true)
    expect(obs.tick(50_000 + 90_000)).toBe(false)
  })

  it('TC-B2-08 onChange exception is swallowed', () => {
    const onChange = vi.fn(() => {
      throw new Error('boom')
    })
    const obs = createThinkingObserver({}, onChange)
    expect(() => obs.notifyStart(100)).not.toThrow()
    expect(obs.isActive(100)).toBe(true)
  })
})
