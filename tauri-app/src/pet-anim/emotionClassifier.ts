// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * emotionClassifier (pet-anim/D1 fallback) — Pet Animation UX v2.
 *
 * Keyword-voting classifier (M-12). When the backend does NOT supply an
 * `emotion` field in chat_v2_final / transcript (D0-05 FAIL → fallback), the
 * frontend classifies the assistant reply text itself.
 *
 * Algorithm (PRD §3 D1 + TDD §2.9):
 *   - Each emotion has a keyword list.
 *   - Each keyword occurrence in the text adds +1 vote for that emotion.
 *   - Tie-break order: sad > happy > angry > surprised > neutral
 *     (PRD intent: when ambiguous, prefer the more "loaded" / "cautious"
 *     emotion so we don't laugh at apologies — AC-10-01 guarantee).
 *   - All zeros → neutral.
 *
 * Disgust/fear (M-13 TODO) are not voted-on by this fallback; backend can
 * still emit them via the main path.
 */

import type { EmotionCode } from './emotionMapper'

/**
 * Keyword lists from TDD §2.9. Chinese only; emoji/punctuation skipped.
 * These intentionally bias toward apologetic / negative keywords being
 * caught (sad/angry) so a sad message is not misclassified happy by the
 * presence of a single "好" inside.
 */
export const HAPPY_KEYWORDS = [
  '好的',
  '没问题',
  '哈',
  '嘻',
  '开心',
  '棒',
  '对',
  '确认',
  '赞',
  '可以',
  '成功',
  '搞定',
  '嗯嗯',
]
export const SAD_KEYWORDS = [
  '抱歉',
  '对不起',
  '遗憾',
  '不好意思',
  '不能',
  '失败',
  '错误',
  '无法',
  '糟',
  '麻烦',
]
export const ANGRY_KEYWORDS = ['不行', '拒绝', '警告', '危险', '立刻', '马上', '禁止']
export const SURPRISED_KEYWORDS = ['突然', '意外', '惊', '哎', '啊', '哦', '咦', '哦哟']

/** Tie-break order (highest priority first). */
const TIE_ORDER: EmotionCode[] = ['sad', 'happy', 'angry', 'surprised']

function countOccurrences(text: string, keywords: string[]): number {
  let n = 0
  for (const kw of keywords) {
    if (!kw) continue
    let idx = text.indexOf(kw)
    while (idx !== -1) {
      n += 1
      idx = text.indexOf(kw, idx + kw.length)
    }
  }
  return n
}

/**
 * Vote across the 4 active emotions. Returns one of:
 *   happy / sad / angry / surprised / neutral
 *
 * AC-10-01 guarantee: "很抱歉，没办法" → sad (NOT happy), because both
 * apology and refusal keywords match `sad` lists.
 */
export function classifyEmotionVoting(text: string): EmotionCode {
  if (typeof text !== 'string' || text.length === 0) return 'neutral'

  const scores: Record<'happy' | 'sad' | 'angry' | 'surprised', number> = {
    happy: countOccurrences(text, HAPPY_KEYWORDS),
    sad: countOccurrences(text, SAD_KEYWORDS),
    angry: countOccurrences(text, ANGRY_KEYWORDS),
    surprised: countOccurrences(text, SURPRISED_KEYWORDS),
  }

  const max = Math.max(scores.happy, scores.sad, scores.angry, scores.surprised)
  if (max === 0) return 'neutral'

  // Tie-break: prefer sad > happy > angry > surprised.
  for (const e of TIE_ORDER) {
    if (e !== 'neutral' && (scores as Record<string, number>)[e] === max) return e
  }
  return 'neutral'
}

export function getEmotionScores(text: string): Record<'happy' | 'sad' | 'angry' | 'surprised', number> {
  return {
    happy: countOccurrences(text, HAPPY_KEYWORDS),
    sad: countOccurrences(text, SAD_KEYWORDS),
    angry: countOccurrences(text, ANGRY_KEYWORDS),
    surprised: countOccurrences(text, SURPRISED_KEYWORDS),
  }
}
