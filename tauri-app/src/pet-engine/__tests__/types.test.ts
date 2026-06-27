// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { describe, it, expect } from 'vitest'
import { PET_ENGINE_BACKENDS } from '../types'
import type { PetEngine, PetEngineBackend, CoreModelLike } from '../types'

describe('PetEngine type', () => {
  it('exports PET_ENGINE_BACKENDS as the canonical runtime union (sprite-only post-S5)', () => {
    expect(PET_ENGINE_BACKENDS).toEqual(['sprite'])
  })

  it('exposes a CoreModelLike via getCoreModel()', () => {
    const stub: PetEngine = {
      backend: 'sprite',
      getCoreModel: () => null,
      playMotion: () => {},
      setExpression: () => {},
      destroy: () => {},
    }
    expect(stub.backend).toBe('sprite')
    expect(stub.getCoreModel()).toBeNull()
  })

  it('PetEngineBackend is a closed union with "sprite" the only member', () => {
    const valid: PetEngineBackend[] = ['sprite']
    expect(valid).toHaveLength(1)
  })

  it('CoreModelLike re-export matches pet-anim contract', () => {
    const m: CoreModelLike = {
      getParameterIndex: () => -1,
      getParameterValueByIndex: () => 0,
      setParameterValueByIndex: () => {},
    }
    expect(m.getParameterIndex('x')).toBe(-1)
  })
})
