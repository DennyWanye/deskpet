// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { CoreModelLike } from '../pet-anim'

export type { CoreModelLike }

/**
 * Backends shipped today (post-S5 Live2D-removal 2026-05-28):
 *  - 'sprite' (default) — self-rendered Canvas2D character ([Live2DCanvas
 *    startCanvas2D]), 100% original code, zero copyright risk.
 *
 * Future:
 *  - 'mesh' — WebGL2 mesh-deformation renderer (future slice, optional
 *    upgrade for better expressiveness).
 *
 * Removed:
 *  - 'live2d' — deleted with cubismcore + pixi-live2d-display + Hiyori
 *    assets in S5. The CoreModelLike abstraction stays so a future
 *    backend can plug in without touching pet-anim.
 */
export type PetEngineBackend = 'sprite'

export const PET_ENGINE_BACKENDS: readonly PetEngineBackend[] = ['sprite'] as const

export interface PetEngine {
  readonly backend: PetEngineBackend
  /**
   * Returns the runtime model the AnimationOverlay should drive each
   * frame, or `null` if not yet loaded. Same object MUST be returned
   * across calls for a stable reference.
   */
  getCoreModel(): CoreModelLike | null
  /** Trigger a named motion group; backends MAY no-op. */
  playMotion(group: string, index?: number): void
  /** Apply a named expression; backends MAY no-op. */
  setExpression(name: string): void
  /** Tear down all GPU/CPU resources; safe to call multiple times. */
  destroy(): void
}
