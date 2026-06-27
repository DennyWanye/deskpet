// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Unit tests for emotionMapper — D1 (TDD §4.8).
 */
import { describe, it, expect } from 'vitest'
import { EMOTION_TABLE, getEmotionParams, isEmotionCode } from '../emotionMapper'

describe('emotionMapper — D1', () => {
  it('TC-D1-01 happy has MouthForm + Smile + Cheek + Brow', () => {
    const p = getEmotionParams('happy')
    expect(p.ParamMouthForm).toBe(0.8)
    expect(p.ParamEyeLSmile).toBe(0.7)
    expect(p.ParamEyeRSmile).toBe(0.7)
    expect(p.ParamCheek).toBe(0.5)
  })

  it('TC-D1-02 sad has negative MouthForm and EyeOpen 0.7 mul + AngleY -3', () => {
    const p = getEmotionParams('sad')
    expect(p.ParamMouthForm).toBe(-0.6)
    expect(p.ParamEyeLOpenMul).toBe(0.7)
    expect(p.ParamEyeROpenMul).toBe(0.7)
    expect(p.ParamAngleY).toBe(-3)
  })

  it('TC-D1-03 angry has high BrowAngle and negative MouthForm', () => {
    const p = getEmotionParams('angry')
    expect(p.ParamBrowLAngle).toBe(1)
    expect(p.ParamBrowRAngle).toBe(1)
    expect(p.ParamMouthForm).toBe(-0.8)
  })

  it('TC-D1-04 disgust / fear are TODO-empty (M-13 placeholders, no throw)', () => {
    expect(getEmotionParams('disgust')).toEqual({})
    expect(getEmotionParams('fear')).toEqual({})
  })

  it('TC-D1-05 neutral is empty', () => {
    expect(getEmotionParams('neutral')).toEqual({})
  })

  it('TC-D1-06 isEmotionCode type guard', () => {
    expect(isEmotionCode('happy')).toBe(true)
    expect(isEmotionCode('disgust')).toBe(true)
    expect(isEmotionCode('whatever')).toBe(false)
    expect(isEmotionCode(undefined)).toBe(false)
    expect(isEmotionCode(null)).toBe(false)
    expect(isEmotionCode(0)).toBe(false)
  })

  it('TC-D1-07 EMOTION_TABLE has all 7 codes', () => {
    expect(Object.keys(EMOTION_TABLE).sort()).toEqual(
      ['angry', 'disgust', 'fear', 'happy', 'neutral', 'sad', 'surprised'],
    )
  })
})
