// SPDX-License-Identifier: BUSL-1.1
import { describe, it, expect } from 'vitest'
import { SpritePetEngine } from '../SpritePetEngine'

describe('SpriteCoreModel as real param dict', () => {
  it('assigns a stable index per param name', () => {
    const m = new SpritePetEngine().getCoreModel()!
    const a = m.getParameterIndex('ParamAngleZ')
    const b = m.getParameterIndex('ParamAngleZ')
    const c = m.getParameterIndex('ParamBodyAngleZ')
    expect(a).toBeGreaterThanOrEqual(0)
    expect(a).toBe(b)
    expect(c).not.toBe(a)
  })
  it('set then get round-trips the value', () => {
    const m = new SpritePetEngine().getCoreModel()!
    const i = m.getParameterIndex('ParamAngleZ')
    m.setParameterValueByIndex(i, 12.5)
    expect(m.getParameterValueByIndex(i)).toBe(12.5)
  })
  it('add accumulates onto current value', () => {
    const m = new SpritePetEngine().getCoreModel()!
    const i = m.getParameterIndex('ParamBustY')
    m.setParameterValueByIndex(i, 0.2)
    m.addParameterValueByIndex!(i, 0.3)
    expect(m.getParameterValueByIndex(i)).toBeCloseTo(0.5)
  })
  it('getParameterValueByIndex returns 0 for an untouched / unknown index', () => {
    const m = new SpritePetEngine().getCoreModel()!
    expect(m.getParameterValueByIndex(999)).toBe(0)
  })
  it('never throws on out-of-range / NaN writes', () => {
    const m = new SpritePetEngine().getCoreModel()!
    expect(() => m.setParameterValueByIndex(-1, 0.5)).not.toThrow()
    expect(() => m.addParameterValueByIndex!(999, NaN)).not.toThrow()
  })
  it('resetParameters zeros all values (prevents ADD accumulation each frame)', () => {
    const m = new SpritePetEngine().getCoreModel() as unknown as {
      getParameterIndex(n: string): number
      setParameterValueByIndex(i: number, v: number): void
      getParameterValueByIndex(i: number): number
      resetParameters(): void
    }
    const i = m.getParameterIndex('ParamBodyAngleZ')
    m.setParameterValueByIndex(i, 8)
    m.resetParameters()
    expect(m.getParameterValueByIndex(i)).toBe(0)
  })
})
