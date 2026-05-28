// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * emotionMapper (pet-anim/D1) — Pet Animation UX v2.
 *
 * Maps an EmotionCode to a Hiyori parameter target set (PRD §3 D1 / TDD §2.8).
 * The overlay then writes these values in applyTo step 7-9 (face params SET +
 * eye MUL chain), subject to the §6.2 priority matrix.
 *
 * EmotionCode set (M-13): happy/sad/angry/surprised/neutral implemented +
 * disgust/fear TODO 占位 (return empty object so the API surface is stable
 * for future expansion without breaking callers).
 *
 * Lock semantics: the emotion is "held" from chat_v2_final until tts_end /
 * tts_interrupt / next_chat_send. The lock is managed at App.tsx level via
 * setEmotion / next-chat handler; this module is pure mapping.
 */

export type EmotionCode =
  | 'happy'
  | 'sad'
  | 'angry'
  | 'surprised'
  | 'neutral'
  | 'disgust'
  | 'fear'

export interface EmotionParams {
  ParamMouthForm?: number
  ParamEyeLSmile?: number
  ParamEyeRSmile?: number
  ParamCheek?: number
  /** Multiplier applied to ParamEyeLOpen via MUL chain (PRD §6.2). */
  ParamEyeLOpenMul?: number
  ParamEyeROpenMul?: number
  ParamBrowLY?: number
  ParamBrowRY?: number
  ParamBrowLAngle?: number
  ParamBrowRAngle?: number
  /** Additive head pitch (D1 sad: -3°). */
  ParamAngleY?: number
  ParamMouthOpenY?: number
}

/**
 * Five fully implemented + two TODO placeholders.
 * Values per TDD §2.8 / PRD §3 D1.
 */
export const EMOTION_TABLE: Record<EmotionCode, EmotionParams> = {
  happy: {
    ParamMouthForm: 0.8,
    ParamEyeLSmile: 0.7,
    ParamEyeRSmile: 0.7,
    ParamCheek: 0.5,
    ParamBrowLY: 0.2,
    ParamBrowRY: 0.2,
  },
  sad: {
    ParamMouthForm: -0.6,
    ParamEyeLOpenMul: 0.7,
    ParamEyeROpenMul: 0.7,
    ParamAngleY: -3,
    ParamBrowLAngle: -0.5,
    ParamBrowRAngle: -0.5,
  },
  angry: {
    ParamBrowLAngle: 1,
    ParamBrowRAngle: 1,
    ParamMouthForm: -0.8,
    ParamEyeLOpenMul: 1.2,
    ParamEyeROpenMul: 1.2,
  },
  surprised: {
    ParamEyeLOpenMul: 1.5,
    ParamEyeROpenMul: 1.5,
    ParamMouthOpenY: 0.4,
    ParamBrowLY: 0.6,
    ParamBrowRY: 0.6,
  },
  neutral: {},
  disgust: {}, // TODO M-13 — params TBD
  fear: {}, // TODO M-13 — params TBD
}

export function getEmotionParams(emotion: EmotionCode): EmotionParams {
  return EMOTION_TABLE[emotion] ?? {}
}

const ALL_EMOTIONS: EmotionCode[] = [
  'happy',
  'sad',
  'angry',
  'surprised',
  'neutral',
  'disgust',
  'fear',
]

export function isEmotionCode(v: unknown): v is EmotionCode {
  return typeof v === 'string' && (ALL_EMOTIONS as string[]).includes(v)
}
