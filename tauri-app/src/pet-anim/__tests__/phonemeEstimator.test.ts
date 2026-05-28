// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Unit tests for phonemeEstimator — B3 fallback (TDD §4.4-b).
 */
import { describe, it, expect } from 'vitest'
import { createPhonemeEstimator, pinyinToViseme } from '../phonemeEstimator'

describe('phonemeEstimator — B3 fallback', () => {
  it('TC-B3f-01 "妈妈" → A frames', () => {
    const est = createPhonemeEstimator()
    const frames = est.estimate('妈妈', 400, 0)
    // dedup of identical consecutive: one frame at t=0 + trailing silent at t=400
    expect(frames.length).toBeGreaterThanOrEqual(2)
    expect(frames[0].v).toBe('A')
    expect(frames[frames.length - 1].v).toBe('silent')
  })

  it('TC-B3f-02 "你好" → I then A→O', () => {
    const est = createPhonemeEstimator()
    const frames = est.estimate('你好', 400, 0)
    // 你 → ni → I; 好 → hao → A
    const visemes = frames.map((f) => f.v)
    expect(visemes).toContain('I')
    expect(visemes).toContain('A')
  })

  it('TC-B3f-03 total runtime ≈ total_duration_ms', () => {
    const est = createPhonemeEstimator()
    const frames = est.estimate('你好妈妈', 800, 0)
    const last = frames[frames.length - 1]
    expect(last.t_ms).toBeCloseTo(800, 0)
  })

  it('TC-B3f-04 punctuation → silent', () => {
    const est = createPhonemeEstimator()
    const frames = est.estimate('好，吗？', 600, 0)
    const visemes = frames.map((f) => f.v)
    expect(visemes).toContain('silent')
    expect(visemes).toContain('A')
  })

  it('TC-B3f-05 unknown char does not throw and falls to silent', () => {
    const est = createPhonemeEstimator()
    // 漾 is not in the minimal table
    expect(() => est.estimate('漾', 200, 0)).not.toThrow()
    const frames = est.estimate('漾', 200, 0)
    expect(frames.every((f) => f.v === 'silent')).toBe(true)
  })

  it('TC-B3f-06 ASCII letter "K 歌" handled', () => {
    const est = createPhonemeEstimator()
    const frames = est.estimate('K歌', 400, 0)
    // K starts with K, no vowel found early → falls through silent; 歌 not in dict → silent
    // Acceptance: doesn't throw; emits at least one frame.
    expect(frames.length).toBeGreaterThan(0)
  })

  it('TC-B3f-07 empty / zero-duration → []', () => {
    const est = createPhonemeEstimator()
    expect(est.estimate('', 200, 0)).toEqual([])
    expect(est.estimate('你好', 0, 0)).toEqual([])
  })

  it('TC-B3f-08 user-supplied dict extends coverage', () => {
    const est = createPhonemeEstimator({
      pinyin_dict: { 漾: 'yang' },
    })
    const frames = est.estimate('漾', 200, 0)
    // yang → A
    expect(frames[0].v).toBe('A')
  })

  it('TC-B3f-09 pinyinToViseme: first vowel cluster decides', () => {
    expect(pinyinToViseme('ma')).toBe('A')
    expect(pinyinToViseme('ni')).toBe('I')
    expect(pinyinToViseme('wo')).toBe('O')
    expect(pinyinToViseme('shi')).toBe('I')
    expect(pinyinToViseme('xie')).toBe('I')
    expect(pinyinToViseme('xyz')).toBe('silent')
  })

  it('TC-B3f-10 start_t_ms offset applies', () => {
    const est = createPhonemeEstimator()
    const frames = est.estimate('好', 200, 1000)
    expect(frames[0].t_ms).toBe(1000)
    const last = frames[frames.length - 1]
    expect(last.t_ms).toBeCloseTo(1200, 0)
  })
})
