/**
 * Unit tests for emotionClassifier — D1 fallback voting (TDD §4.9).
 */
import { describe, it, expect } from 'vitest'
import { classifyEmotionVoting, getEmotionScores } from '../emotionClassifier'

describe('emotionClassifier — D1 fallback voting', () => {
  it('TC-D1c-01 "好的" → happy', () => {
    expect(classifyEmotionVoting('好的')).toBe('happy')
  })

  it('TC-D1c-02 "抱歉" → sad', () => {
    expect(classifyEmotionVoting('抱歉')).toBe('sad')
  })

  it('TC-D1c-03 "不行" → angry', () => {
    expect(classifyEmotionVoting('不行')).toBe('angry')
  })

  it('TC-D1c-04 "突然" → surprised', () => {
    expect(classifyEmotionVoting('突然')).toBe('surprised')
  })

  it('TC-D1c-05 "今天天气不错" → neutral', () => {
    expect(classifyEmotionVoting('今天天气不错')).toBe('neutral')
  })

  it('TC-D1c-06 投票平票 → sad 优先 (AC-10-01)', () => {
    // 抱歉 (sad +1), 没问题 (happy +1) → tie → sad wins
    expect(classifyEmotionVoting('抱歉，没问题')).toBe('sad')
  })

  it('TC-D1c-07 多票一边倒 → happy', () => {
    // 好的 (happy +1), 棒 (happy +1), 可以 (happy +1) → happy 3
    expect(classifyEmotionVoting('好的，棒，可以')).toBe('happy')
  })

  it('TC-D1c-08 "很抱歉，没办法" → sad (AC-10-01 guarantee)', () => {
    // sad keywords: 抱歉, 不能/无法 (无法 matches)
    expect(classifyEmotionVoting('很抱歉，没办法')).toBe('sad')
  })

  it('TC-D1c-09 empty / non-string → neutral', () => {
    expect(classifyEmotionVoting('')).toBe('neutral')
    expect(classifyEmotionVoting(null as never)).toBe('neutral')
    expect(classifyEmotionVoting(123 as never)).toBe('neutral')
  })

  it('TC-D1c-10 getEmotionScores returns vote counts', () => {
    const s = getEmotionScores('好的，没问题')
    expect(s.happy).toBeGreaterThanOrEqual(2)
    expect(s.sad).toBe(0)
  })
})
