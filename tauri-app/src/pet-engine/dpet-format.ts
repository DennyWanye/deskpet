// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * .dpet — DeskPet's own model format (S2 placeholder).
 *
 * Designed from scratch with zero overlap with Live2D's .moc3 /
 * .model3.json schemas to avoid any IP-derivation claims. The runtime
 * is bring-your-own (a future 'mesh' backend can implement this; the
 * shipping 'sprite' backend ignores it).
 *
 * v0 (minimal): metadata + sprite-layer list with simple keyframe
 * sequences. v1 (future) will add bone skeleton + mesh deformation +
 * morph targets + parameter curves.
 */

export interface DpetSpriteLayer {
  /** Unique within a model. Referenced by motion keyframes. */
  readonly id: string
  /** Relative asset path (PNG / SVG) or `null` for procedurally-drawn layers. */
  readonly asset: string | null
  /** Anchor offset from model origin, in CSS px. */
  readonly anchor: { x: number; y: number }
  /** Stacking order — higher draws on top. */
  readonly z: number
}

export interface DpetParamCurve {
  /** Parameter name pet-anim writes (e.g. "ParamMouthOpenY"). */
  readonly param: string
  /** Curve type: linear lerp, ease-in/out, hold. */
  readonly easing: 'linear' | 'ease_in' | 'ease_out' | 'hold'
  /** [t (ms), value] keyframes sorted ascending. */
  readonly keyframes: readonly [number, number][]
}

export interface DpetMotion {
  readonly id: string
  /** Total duration in ms. */
  readonly duration: number
  /** Loop or play-once. */
  readonly mode: 'loop' | 'oneshot'
  /** Curves driven by this motion. */
  readonly curves: readonly DpetParamCurve[]
}

export interface DpetModel {
  /** Always "dpet" for forward-compat sniffing. */
  readonly magic: 'dpet'
  /** Schema version — major.minor. Loader rejects unknown majors. */
  readonly version: '0.1'
  /** Display name (no copyright implications). */
  readonly name: string
  /** Original author / asset license. Loader REQUIRES this — pet-engine
   *  refuses to load a model without a license string, to keep the
   *  zero-copyright invariant honest. */
  readonly license: string
  readonly layers: readonly DpetSpriteLayer[]
  readonly motions: readonly DpetMotion[]
}

/**
 * Validate a parsed JSON blob conforms to the DpetModel shape.
 * Returns the typed model or throws with the first violation.
 *
 * Deliberately strict on `license` and `magic` — these are the two
 * fields whose absence indicates "this is not a deskpet model" or
 * "the model author forgot to declare a license", both of which are
 * blockers we want to fail fast on.
 */
export function validateDpetModel(raw: unknown): DpetModel {
  if (typeof raw !== 'object' || raw === null) {
    throw new Error('[dpet] not an object')
  }
  const r = raw as Record<string, unknown>
  if (r.magic !== 'dpet') {
    throw new Error(`[dpet] missing or wrong magic: expected "dpet", got ${JSON.stringify(r.magic)}`)
  }
  if (r.version !== '0.1') {
    throw new Error(`[dpet] unsupported version: ${JSON.stringify(r.version)}`)
  }
  if (typeof r.license !== 'string' || r.license.length === 0) {
    throw new Error('[dpet] license is required (use "CC0", "MIT", "BUSL-1.1", "original-work-by-…" etc.)')
  }
  if (typeof r.name !== 'string') throw new Error('[dpet] name must be a string')
  if (!Array.isArray(r.layers)) throw new Error('[dpet] layers must be an array')
  if (!Array.isArray(r.motions)) throw new Error('[dpet] motions must be an array')
  return raw as DpetModel
}
