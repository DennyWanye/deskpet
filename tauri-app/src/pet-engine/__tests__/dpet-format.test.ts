// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { describe, it, expect } from 'vitest'
import { validateDpetModel, type DpetModel } from '../dpet-format'

const valid: DpetModel = {
  magic: 'dpet',
  version: '0.1',
  name: 'PurpleCat',
  license: 'original-work-by-DennyWanye-BUSL-1.1',
  layers: [
    { id: 'head', asset: null, anchor: { x: 0, y: 0 }, z: 10 },
  ],
  motions: [
    {
      id: 'idle',
      duration: 2000,
      mode: 'loop',
      curves: [
        { param: 'ParamMouthOpenY', easing: 'linear', keyframes: [[0, 0], [1000, 0.3], [2000, 0]] },
      ],
    },
  ],
}

describe('validateDpetModel', () => {
  it('accepts a well-formed model', () => {
    expect(validateDpetModel(valid)).toBe(valid)
  })

  it('rejects null / non-object input', () => {
    expect(() => validateDpetModel(null)).toThrow(/not an object/)
    expect(() => validateDpetModel('hi')).toThrow(/not an object/)
  })

  it('rejects missing or wrong magic', () => {
    expect(() => validateDpetModel({ ...valid, magic: 'live2d' })).toThrow(/magic/)
    expect(() => validateDpetModel({ ...valid, magic: undefined })).toThrow(/magic/)
  })

  it('rejects unsupported version', () => {
    expect(() => validateDpetModel({ ...valid, version: '99.0' })).toThrow(/version/)
  })

  it('REQUIRES a license declaration (zero-copyright invariant)', () => {
    expect(() => validateDpetModel({ ...valid, license: '' })).toThrow(/license is required/)
    expect(() => validateDpetModel({ ...valid, license: undefined as any })).toThrow(/license is required/)
  })

  it('rejects non-array layers / motions', () => {
    expect(() => validateDpetModel({ ...valid, layers: 'not-array' as any })).toThrow(/layers/)
    expect(() => validateDpetModel({ ...valid, motions: 42 as any })).toThrow(/motions/)
  })
})
