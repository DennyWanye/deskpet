/**
 * Unit tests for edgeWatcher — E1 (TDD §4.10).
 */
import { describe, it, expect } from 'vitest'
import { pickEdge, poseForEdge, snapTarget } from '../edgeWatcher'

describe('edgeWatcher — E1', () => {
  it('TC-E1-01 pet near right edge → "right"', () => {
    expect(pickEdge({ x: 1800, y: 400, w: 100, h: 100 }, { width: 1920, height: 1080 })).toBe('right')
  })

  it('TC-E1-02 pet near left edge → "left"', () => {
    expect(pickEdge({ x: 0, y: 400, w: 100, h: 100 }, { width: 1920, height: 1080 })).toBe('left')
  })

  it('TC-E1-03 pet near top edge → "top"', () => {
    expect(pickEdge({ x: 800, y: 10, w: 100, h: 100 }, { width: 1920, height: 1080 })).toBe('top')
  })

  it('TC-E1-04 pet near bottom edge → "bottom"', () => {
    expect(pickEdge({ x: 800, y: 1020, w: 100, h: 100 }, { width: 1920, height: 1080 })).toBe('bottom')
  })

  it('TC-E1-05 pet in middle → null', () => {
    expect(pickEdge({ x: 900, y: 500, w: 100, h: 100 }, { width: 1920, height: 1080 })).toBe(null)
  })

  it('TC-E1-06 poseForEdge mapping', () => {
    expect(poseForEdge('right')).toBe(90)
    expect(poseForEdge('left')).toBe(-90)
    expect(poseForEdge('top')).toBe(0)
    expect(poseForEdge('bottom')).toBe(180)
    expect(poseForEdge(null)).toBe(0)
  })

  it('TC-E1-07 snapTarget for right edge', () => {
    const t = snapTarget({ x: 1800, y: 400, w: 100, h: 100 }, { width: 1920, height: 1080 }, 'right')
    expect(t).not.toBeNull()
    expect(t!.x).toBe(1920 - 100 + 10) // 1830 — push 10px out past right edge
  })

  it('TC-E1-08 closer-edge tiebreak', () => {
    // 5px from top, 95px from left — top wins
    expect(pickEdge({ x: 90, y: 5, w: 50, h: 50 }, { width: 1920, height: 1080 })).toBe('top')
  })

  it('TC-E1-09 invalid input → null safely', () => {
    expect(pickEdge({ x: NaN, y: 0, w: 100, h: 100 }, { width: 1920, height: 1080 })).toBe(null)
  })
})
