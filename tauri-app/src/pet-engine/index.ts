// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import type { PetEngine, PetEngineBackend, CoreModelLike } from './types'
import { PET_ENGINE_BACKENDS } from './types'
import { SpritePetEngine } from './SpritePetEngine'

export type { PetEngine, PetEngineBackend, CoreModelLike }
export { PET_ENGINE_BACKENDS, SpritePetEngine }

export interface PetEngineFactoryOpts {
  /* Reserved for future backends that need init data (e.g. a future
     'mesh' backend may take a .dpet model handle). 'sprite' takes none. */
}

export function createPetEngine(
  backend: PetEngineBackend,
  _opts: PetEngineFactoryOpts = {},
): PetEngine {
  switch (backend) {
    case 'sprite':
      return new SpritePetEngine()
    default: {
      const _exhaustive: never = backend
      throw new Error(`[pet-engine] unknown backend: ${String(_exhaustive)}`)
    }
  }
}

const KNOWN: ReadonlySet<string> = new Set<PetEngineBackend>(PET_ENGINE_BACKENDS)

/**
 * Resolve VITE_PET_ENGINE (or any string source) into a known backend.
 * Defaults to 'sprite' (the only shipped backend post-S5). Unknown
 * values warn and still fall through to 'sprite'.
 */
export function resolveBackendFromEnv(raw: string | undefined): PetEngineBackend {
  if (!raw) return 'sprite'
  if (KNOWN.has(raw)) return raw as PetEngineBackend
  console.warn(`[pet-engine] unknown VITE_PET_ENGINE="${raw}", falling back to sprite`)
  return 'sprite'
}
