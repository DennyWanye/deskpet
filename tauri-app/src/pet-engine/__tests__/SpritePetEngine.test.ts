// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { describe, it, expect } from 'vitest'
import { SpritePetEngine } from '../SpritePetEngine'

describe('SpritePetEngine', () => {
  it('reports backend="sprite"', () => {
    const eng = new SpritePetEngine()
    expect(eng.backend).toBe('sprite')
  })

  it('returns a stable CoreModelLike from getCoreModel()', () => {
    const eng = new SpritePetEngine()
    const m1 = eng.getCoreModel()
    const m2 = eng.getCoreModel()
    expect(m1).not.toBeNull()
    expect(m1).toBe(m2)
  })

  it('getParameterIndex assigns a stable non-negative index per param name', () => {
    // SpriteCoreModel is now a real param dict (sprite renderer reads
    // writes back via deriveTransform), so indices are allocated lazily
    // and reused — no longer the old -1 no-op sentinel.
    const eng = new SpritePetEngine()
    const m = eng.getCoreModel()!
    const a = m.getParameterIndex('ParamAngleX')
    const b = m.getParameterIndex('ParamMouthOpenY')
    expect(a).toBeGreaterThanOrEqual(0)
    expect(b).toBeGreaterThanOrEqual(0)
    expect(a).not.toBe(b)
    expect(m.getParameterIndex('ParamAngleX')).toBe(a)
  })

  it('setParameterValueByIndex never throws even for -1 / out-of-range idx', () => {
    const eng = new SpritePetEngine()
    const m = eng.getCoreModel()!
    expect(() => m.setParameterValueByIndex(-1, 0.5)).not.toThrow()
    expect(() => m.setParameterValueByIndex(999, 0.5)).not.toThrow()
    expect(() => m.setParameterValueByIndex(0, NaN)).not.toThrow()
  })

  it('addParameterValueByIndex is exposed (pet-anim fast-path)', () => {
    const eng = new SpritePetEngine()
    const m = eng.getCoreModel()!
    expect(typeof m.addParameterValueByIndex).toBe('function')
    expect(() => m.addParameterValueByIndex!(-1, 0.5)).not.toThrow()
  })

  it('playMotion and setExpression are silent no-ops (no warn spam)', () => {
    const eng = new SpritePetEngine()
    expect(() => {
      eng.playMotion('Idle')
      eng.playMotion('TapBody', 2)
      eng.setExpression('happy')
    }).not.toThrow()
  })

  it('destroy() makes subsequent getCoreModel() return null', () => {
    const eng = new SpritePetEngine()
    expect(eng.getCoreModel()).not.toBeNull()
    eng.destroy()
    expect(eng.getCoreModel()).toBeNull()
  })

  it('destroy() is idempotent', () => {
    const eng = new SpritePetEngine()
    eng.destroy()
    expect(() => eng.destroy()).not.toThrow()
    expect(eng.getCoreModel()).toBeNull()
  })
})
