// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Unit tests for milestoneClient — D2 client-side (TDD §4.15-b TC-D2-06 focus).
 */
import { describe, it, expect, vi } from 'vitest'
import { createMilestoneClient, type MilestoneEvent } from '../milestoneClient'

const EV_7D: MilestoneEvent = { kind: 'streak_7d', message: '连续 7 天聊天！', achieved_at: 0 }
const EV_1000: MilestoneEvent = { kind: 'msgs_1000', message: '累计 1000 条消息！', achieved_at: 0 }

describe('milestoneClient — D2', () => {
  it('TC-D2-01 enqueue then tick → onCelebrationStart fires', () => {
    const start = vi.fn()
    const c = createMilestoneClient({ celebration_ms: 3000 }, { onCelebrationStart: start })
    let s = c.init()
    s = c.enqueue(s, EV_7D)
    s = c.tick(s, 0)
    expect(start).toHaveBeenCalledWith(EV_7D, 0)
    expect(s.active?.kind).toBe('streak_7d')
  })

  it('TC-D2-02 active expires after celebration_ms → onCelebrationEnd', () => {
    const end = vi.fn()
    const c = createMilestoneClient({ celebration_ms: 3000 }, { onCelebrationEnd: end })
    let s = c.init()
    s = c.enqueue(s, EV_7D)
    s = c.tick(s, 0)
    s = c.tick(s, 3000)
    expect(end).toHaveBeenCalledWith(EV_7D, 3000)
    expect(s.active).toBe(null)
  })

  it('TC-D2-06 two milestones same instant → FIFO, no concurrent (M-14)', () => {
    const start = vi.fn()
    const end = vi.fn()
    const c = createMilestoneClient(
      { celebration_ms: 3000 },
      { onCelebrationStart: start, onCelebrationEnd: end },
    )
    let s = c.init()
    s = c.enqueue(s, EV_7D)
    s = c.enqueue(s, EV_1000)
    s = c.tick(s, 0)
    expect(start.mock.calls.length).toBe(1)
    expect(start.mock.calls[0][0]).toBe(EV_7D)
    // No concurrent — second is queued.
    expect(s.active?.kind).toBe('streak_7d')
    expect(s.queue.length).toBe(1)

    // Advance past first celebration.
    s = c.tick(s, 3000)
    expect(end.mock.calls.length).toBe(1)
    expect(start.mock.calls.length).toBe(2)
    expect(start.mock.calls[1][0]).toBe(EV_1000)
    expect(s.active?.kind).toBe('msgs_1000')
  })

  it('TC-D2-07 invalid enqueue input → no-op', () => {
    const c = createMilestoneClient()
    let s = c.init()
    s = c.enqueue(s, null as never)
    s = c.enqueue(s, { kind: 0 as never, message: 'x', achieved_at: 0 })
    expect(s.queue.length).toBe(0)
  })
})
