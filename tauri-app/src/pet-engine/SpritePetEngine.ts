// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { CoreModelLike, PetEngine, PetEngineBackend } from './types'

/**
 * Sprite CoreModel — a real parameter dictionary.
 *
 * pet-anim's AnimationOverlay calls getParameterIndex(name) to obtain a
 * stable per-name slot, then set/add ParameterValueByIndex each frame
 * (ParamAngleZ / ParamBodyAngleZ / ParamBustY / ParamBodyAngleX / ...).
 * Those values are stored here and read back by the sprite renderer
 * (via deriveTransform in components/petTransform.ts) to compute the
 *立绘 overall transform — so interactions are now visible.
 *
 * Index assignment is lazy: the first getParameterIndex(name) call
 * allocates the next free slot (initialised to 0) and reuses it on
 * subsequent calls for the same name. Writes MUST never throw — out-of-
 * range indices and non-finite values are silently ignored.
 */
class SpriteCoreModel implements CoreModelLike {
  private nameToIdx = new Map<string, number>()
  private values: number[] = []

  getParameterIndex(name: string): number {
    let idx = this.nameToIdx.get(name)
    if (idx === undefined) {
      idx = this.values.length
      this.nameToIdx.set(name, idx)
      this.values.push(0)
    }
    return idx
  }
  setParameterValueByIndex(idx: number, val: number): void {
    if (idx < 0 || !Number.isFinite(val)) return
    this.values[idx] = val
  }
  addParameterValueByIndex(idx: number, val: number): void {
    if (idx < 0 || !Number.isFinite(val)) return
    this.values[idx] = (this.values[idx] ?? 0) + val
  }
  getParameterValueByIndex(idx: number): number {
    return this.values[idx] ?? 0
  }
  /**
   * Zero every parameter. The renderer MUST call this once per frame
   * BEFORE AnimationOverlay.applyTo(): pet-anim writes most params
   * (perlin / wobble / lean / squash → BodyAngleZ/X, BustY) with ADD
   * semantics, assuming a Cubism-style per-frame reset to baseline.
   * Without this reset, every ADD accumulates frame over frame and the
   * figure drifts / tilts forever instead of settling back.
   */
  resetParameters(): void {
    this.values.fill(0)
  }
}

/**
 * The sprite backend — production rendering path post-S5.
 *
 * Live2DCanvas.startCanvas2D() paints a 100%-original Canvas2D
 * character (purple cat); this engine provides the CoreModelLike
 * surface pet-anim wants. Parameter writes now land in a real
 * parameter dict (SpriteCoreModel) that the sprite renderer reads
 * back each frame via deriveTransform — so pet-anim interactions
 * drive the立绘's overall transform.
 *
 * Zero copyright risk: every byte of this file + the Canvas2D drawing
 * code is original work; no Live2D Cubism SDK, no Hiyori assets, no
 * proprietary mesh format.
 */
export class SpritePetEngine implements PetEngine {
  readonly backend: PetEngineBackend = 'sprite'
  private model: CoreModelLike | null = new SpriteCoreModel()

  getCoreModel(): CoreModelLike | null {
    return this.model
  }

  playMotion(_group: string, _index?: number): void {
    /* TODO S4: motion keyframe player — for now, the Canvas2D
       character runs a continuous idle loop and ignores group calls. */
  }

  setExpression(_name: string): void {
    /* TODO S4: expression sprite-swap — for now, the Canvas2D
       character has a single neutral expression. */
  }

  destroy(): void {
    this.model = null
  }
}
