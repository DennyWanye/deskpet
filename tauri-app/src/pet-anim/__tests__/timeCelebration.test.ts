/**
 * Unit tests for timeCelebration — C3 (TDD §4.7).
 */
import { describe, it, expect, vi } from 'vitest'
import { createTimeCelebration } from '../timeCelebration'

function dateAt(year: number, month: number, day: number, hour: number, minute: number): Date {
  return new Date(year, month - 1, day, hour, minute, 0, 0)
}

describe('timeCelebration — C3', () => {
  it('TC-C3-01 fires hourly at minute=0', () => {
    let now = dateAt(2026, 5, 26, 12, 0)
    const cb = vi.fn()
    const c = createTimeCelebration({ clock: () => now }, cb)
    c.tick(performance.now())
    expect(cb).toHaveBeenCalledTimes(1)
    expect(cb.mock.calls[0][0]).toBe('hourly')
  })

  it('TC-C3-02 does not fire when minute != 0', () => {
    let now = dateAt(2026, 5, 26, 12, 30)
    const cb = vi.fn()
    const c = createTimeCelebration({ clock: () => now }, cb)
    c.tick(performance.now())
    expect(cb).not.toHaveBeenCalled()
  })

  it('TC-C3-03 anniversary at midnight fires "anniversary" kind', () => {
    const now = dateAt(2026, 5, 25, 0, 0)
    const cb = vi.fn()
    createTimeCelebration(
      {
        clock: () => now,
        anniversaries: [{ date: '05-25', message: '桌宠生日' }],
      },
      cb,
    ).tick(performance.now())
    expect(cb).toHaveBeenCalledWith('anniversary', '桌宠生日', expect.any(Number))
  })

  it('TC-C3-04 DND-active suppresses hourly (M-9)', () => {
    const now = dateAt(2026, 5, 26, 14, 0)
    const cb = vi.fn()
    const c = createTimeCelebration(
      {
        clock: () => now,
        dnd_check: () => true,
      },
      cb,
    )
    c.tick(performance.now())
    expect(cb).not.toHaveBeenCalled()
    expect(c.debug().last_skipped_for_dnd).toBe(true)
  })

  it('TC-C3-05 DND does NOT suppress anniversary (重要日子)', () => {
    const now = dateAt(2026, 5, 25, 0, 0)
    const cb = vi.fn()
    const c = createTimeCelebration(
      {
        clock: () => now,
        anniversaries: [{ date: '05-25', message: '桌宠生日' }],
        dnd_check: () => true,
      },
      cb,
    )
    c.tick(performance.now())
    expect(cb).toHaveBeenCalledTimes(1)
    expect(cb.mock.calls[0][0]).toBe('anniversary')
  })

  it('TC-C3-06 same hour does not double-fire on repeated ticks', () => {
    const now = dateAt(2026, 5, 26, 12, 0)
    const cb = vi.fn()
    const c = createTimeCelebration({ clock: () => now }, cb)
    c.tick(performance.now())
    c.tick(performance.now() + 100)
    c.tick(performance.now() + 200)
    expect(cb).toHaveBeenCalledTimes(1)
  })
})
