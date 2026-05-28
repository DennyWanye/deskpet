// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Unit tests for idleWatcher — C1 + C2 (TDD §4.6).
 */
import { describe, it, expect, vi } from 'vitest'
import { createIdleWatcher, pickWelcomeIntensity } from '../idleWatcher'

describe('idleWatcher — C1/C2', () => {
  it('TC-C1-01 init → not in low_energy; last_activity_t = -Infinity', () => {
    const w = createIdleWatcher()
    const s = w.init()
    expect(s.low_energy).toBe(false)
    expect(s.last_activity_t).toBe(-Infinity)
  })

  it('TC-C1-02 first activity sets last_activity_t', () => {
    const w = createIdleWatcher()
    let s = w.init()
    s = w.notifyActivity(s, 1000)
    expect(s.last_activity_t).toBe(1000)
    expect(s.low_energy).toBe(false)
  })

  it('TC-C1-03 tick past low_energy_threshold_ms triggers low_energy', () => {
    const onLow = vi.fn()
    const w = createIdleWatcher({ low_energy_threshold_ms: 300_000 }, { onLowEnergy: onLow })
    let s = w.init()
    s = w.notifyActivity(s, 0)
    s = w.tick(s, 299_999)
    expect(s.low_energy).toBe(false)
    expect(onLow).not.toHaveBeenCalled()

    s = w.tick(s, 300_000)
    expect(s.low_energy).toBe(true)
    expect(s.low_energy_start_t).toBe(300_000)
    expect(onLow).toHaveBeenCalledWith(300_000)
  })

  it('TC-C2-01 wakeup from low_energy < 15min → "normal"', () => {
    const onWake = vi.fn()
    const w = createIdleWatcher(
      { low_energy_threshold_ms: 300_000 },
      { onWakeup: onWake },
    )
    let s = w.init()
    s = w.notifyActivity(s, 0)
    s = w.tick(s, 300_000) // enter low_energy
    s = w.notifyActivity(s, 300_000 + 600_000) // wake at 10min into low_energy
    expect(s.low_energy).toBe(false)
    expect(onWake).toHaveBeenCalledTimes(1)
    expect(onWake.mock.calls[0][2]).toBe('normal')
  })

  it('TC-C2-02 wakeup 15min ≤ duration < 1h → "bubble"', () => {
    const onWake = vi.fn()
    const w = createIdleWatcher({}, { onWakeup: onWake })
    let s = w.init()
    s = w.notifyActivity(s, 0)
    s = w.tick(s, 300_000)
    s = w.notifyActivity(s, 300_000 + 30 * 60_000) // 30min low_energy
    expect(onWake.mock.calls[0][2]).toBe('bubble')
  })

  it('TC-C2-03 wakeup ≥ 1h → "intense"', () => {
    const onWake = vi.fn()
    const w = createIdleWatcher({}, { onWakeup: onWake })
    let s = w.init()
    s = w.notifyActivity(s, 0)
    s = w.tick(s, 300_000)
    s = w.notifyActivity(s, 300_000 + 90 * 60_000) // 90min low_energy
    expect(onWake.mock.calls[0][2]).toBe('intense')
  })

  it('TC-C2-04 welcome cooldown suppresses rapid second welcome', () => {
    const onWake = vi.fn()
    const w = createIdleWatcher(
      { welcome_cooldown_ms: 60_000 },
      { onWakeup: onWake },
    )
    let s = w.init()
    s = w.notifyActivity(s, 0)
    s = w.tick(s, 300_000)
    // wake
    s = w.notifyActivity(s, 300_000 + 1_000)
    expect(onWake).toHaveBeenCalledTimes(1)
    // immediately go idle again and wake again (within cooldown)
    s = w.tick(s, 600_000) // 5min idle from previous activity
    s = w.notifyActivity(s, 600_000 + 1_000)
    // Inside cooldown — onWakeup NOT called a second time
    expect(onWake).toHaveBeenCalledTimes(1)
  })

  it('TC-C1-06 (M-5) visibility/blur-driven notifyActivity also resets idle', () => {
    // App.tsx will wire visibilitychange / blur etc. to notifyActivity.
    // FSM is event-source-agnostic — assert the contract: any activity
    // bump resets the timer regardless of what triggered it.
    const w = createIdleWatcher({ low_energy_threshold_ms: 300_000 })
    let s = w.init()
    s = w.notifyActivity(s, 0)
    s = w.tick(s, 250_000)
    s = w.notifyActivity(s, 250_000) // simulating visibility event
    s = w.tick(s, 250_000 + 299_999)
    expect(s.low_energy).toBe(false) // still inside window relative to last activity
    s = w.tick(s, 250_000 + 300_000)
    expect(s.low_energy).toBe(true)
  })

  it('TC-C1-07 non-finite now_t → no-op', () => {
    const w = createIdleWatcher()
    const s = w.init()
    expect(w.notifyActivity(s, Number.NaN)).toBe(s)
    expect(w.tick(s, Number.NaN)).toBe(s)
  })

  it('TC-C2-05 pickWelcomeIntensity boundaries', () => {
    expect(pickWelcomeIntensity(0)).toBe('normal')
    expect(pickWelcomeIntensity(900_000 - 1)).toBe('normal')
    expect(pickWelcomeIntensity(900_000)).toBe('bubble')
    expect(pickWelcomeIntensity(3_600_000 - 1)).toBe('bubble')
    expect(pickWelcomeIntensity(3_600_000)).toBe('intense')
  })
})
