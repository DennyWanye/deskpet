/**
 * Unit tests for occlusionWatcher — E2 (TDD §4.11).
 */
import { describe, it, expect, vi } from 'vitest'
import {
  createOcclusionWatcher,
  findSafeSpotGrid,
  overlapRatio,
  type TopWindowInfo,
} from '../occlusionWatcher'

const PET_RECT = { x: 1000, y: 500, w: 200, h: 200 }
const SCREEN = { width: 1920, height: 1080 }

function win(x: number, y: number, w: number, h: number, is_visible = true, hwnd = 1): TopWindowInfo {
  return { hwnd, title: 'w', rect: { x, y, w, h }, is_visible }
}

describe('occlusionWatcher — E2', () => {
  it('TC-E2-01 overlapRatio basic math', () => {
    expect(overlapRatio({ x: 0, y: 0, w: 100, h: 100 }, { x: 50, y: 50, w: 100, h: 100 })).toBeCloseTo(0.25, 4)
    expect(overlapRatio({ x: 0, y: 0, w: 100, h: 100 }, { x: 200, y: 200, w: 100, h: 100 })).toBe(0)
  })

  it('TC-E2-02 grace window not crossed → no onOccluded', async () => {
    const onOccluded = vi.fn()
    const w = createOcclusionWatcher(
      { threshold_ratio: 0.5, grace_ms: 5000 },
      {
        fetchTopWindows: async () => [win(900, 400, 800, 600)],
        onOccluded,
      },
    )
    let s = w.init()
    s = await w.tick(s, 0, PET_RECT, SCREEN)
    s = await w.tick(s, 1000, PET_RECT, SCREEN)
    expect(onOccluded).not.toHaveBeenCalled()
    expect(s.occluded).toBe(false)
  })

  it('TC-E2-03 grace exceeded → onOccluded fires', async () => {
    const onOccluded = vi.fn()
    const w = createOcclusionWatcher(
      { threshold_ratio: 0.5, grace_ms: 5000 },
      {
        fetchTopWindows: async () => [win(900, 400, 800, 600)],
        onOccluded,
      },
    )
    let s = w.init()
    s = await w.tick(s, 0, PET_RECT, SCREEN)
    s = await w.tick(s, 5000, PET_RECT, SCREEN) // 5s elapsed → grace crossed
    expect(onOccluded).toHaveBeenCalledTimes(1)
    expect(s.occluded).toBe(true)
  })

  it('TC-E2-04 occlusion clears → onClear fires', async () => {
    const onClear = vi.fn()
    let occluding = true
    const w = createOcclusionWatcher(
      { threshold_ratio: 0.5, grace_ms: 1000 },
      {
        fetchTopWindows: async () =>
          occluding ? [win(900, 400, 800, 600)] : [win(0, 0, 50, 50)],
        onClear,
      },
    )
    let s = w.init()
    s = await w.tick(s, 0, PET_RECT, SCREEN)
    s = await w.tick(s, 1000, PET_RECT, SCREEN) // → occluded
    occluding = false
    s = await w.tick(s, 2000, PET_RECT, SCREEN)
    expect(onClear).toHaveBeenCalledTimes(1)
    expect(s.occluded).toBe(false)
  })

  it('TC-E2-05 fetchTopWindows throws → silent no-op (graceful degrade)', async () => {
    const onOccluded = vi.fn()
    const w = createOcclusionWatcher(
      { threshold_ratio: 0.5, grace_ms: 1000 },
      {
        fetchTopWindows: async () => {
          throw new Error('Win32 fail')
        },
        onOccluded,
      },
    )
    let s = w.init()
    await expect(w.tick(s, 0, PET_RECT, SCREEN)).resolves.toBeDefined()
    expect(onOccluded).not.toHaveBeenCalled()
  })

  it('TC-E2-06 (M-17) findSafeSpotGrid finds a cell even when corners filled', () => {
    // 4 corners + 4 edge-mids covered → naive 8-point pick would fail.
    const others: TopWindowInfo[] = [
      win(0, 0, 400, 400), // top-left
      win(SCREEN.width - 400, 0, 400, 400), // top-right
      win(0, SCREEN.height - 400, 400, 400), // bottom-left
      win(SCREEN.width - 400, SCREEN.height - 400, 400, 400), // bottom-right
    ]
    const spot = findSafeSpotGrid(PET_RECT, SCREEN, others)
    expect(spot).not.toBeNull()
    // The chosen spot must not push pet off-screen (AC-10-02).
    expect(spot!.x).toBeGreaterThanOrEqual(0)
    expect(spot!.y).toBeGreaterThanOrEqual(0)
    expect(spot!.x + PET_RECT.w).toBeLessThanOrEqual(SCREEN.width)
    expect(spot!.y + PET_RECT.h).toBeLessThanOrEqual(SCREEN.height)
  })

  it('TC-E2-07 findSafeSpotGrid → null when no candidate works', () => {
    // One giant window covering everything.
    const others: TopWindowInfo[] = [win(0, 0, SCREEN.width, SCREEN.height)]
    const spot = findSafeSpotGrid(PET_RECT, SCREEN, others)
    expect(spot).toBeNull()
  })

  it('TC-E2-08 invisible windows are ignored', async () => {
    const onOccluded = vi.fn()
    const w = createOcclusionWatcher(
      { threshold_ratio: 0.5, grace_ms: 1000 },
      {
        fetchTopWindows: async () => [
          { hwnd: 1, title: 'minimised', rect: { x: 900, y: 400, w: 800, h: 600 }, is_visible: false },
        ],
        onOccluded,
      },
    )
    let s = w.init()
    s = await w.tick(s, 0, PET_RECT, SCREEN)
    s = await w.tick(s, 2000, PET_RECT, SCREEN)
    expect(onOccluded).not.toHaveBeenCalled()
  })
})
