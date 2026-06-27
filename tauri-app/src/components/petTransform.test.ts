// SPDX-License-Identifier: BUSL-1.1
import { describe, it, expect } from 'vitest'
import { deriveTransform, type PetTransform } from './petTransform'
import type { CoreModelLike } from '../pet-engine'

function fakeModel(params: Record<string, number>): CoreModelLike {
  const names = Object.keys(params)
  return {
    getParameterIndex: (n: string) => names.indexOf(n),
    setParameterValueByIndex: () => {},
    addParameterValueByIndex: () => {},
    getParameterValueByIndex: (i: number) => params[names[i]] ?? 0,
  }
}

describe('deriveTransform', () => {
  it('neutral model → identity transform', () => {
    expect(deriveTransform(fakeModel({}))).toEqual<PetTransform>({ rotateDeg: 0, offsetX: 0, offsetY: 0, scaleX: 1, scaleY: 1 })
  })
  it('AngleZ + BodyAngleZ 合成 rotate', () => {
    expect(deriveTransform(fakeModel({ ParamAngleZ: 5, ParamBodyAngleZ: 8 })).rotateDeg).toBeCloseTo(13)
  })
  it('rotate clamp 到 ±25', () => {
    expect(deriveTransform(fakeModel({ ParamAngleZ: 40, ParamBodyAngleZ: 40 })).rotateDeg).toBe(25)
  })
  it('BodyAngleX → offsetX (度*0.5)', () => {
    expect(deriveTransform(fakeModel({ ParamBodyAngleX: 18 })).offsetX).toBeCloseTo(9)
  })
  it('BustY>0: scaleY>1, scaleX<1', () => {
    const tf = deriveTransform(fakeModel({ ParamBustY: 1 }))
    expect(tf.scaleY).toBeCloseTo(1.12); expect(tf.scaleX).toBeCloseTo(0.94)
  })
  it('BustY<0: scaleY<1, scaleX>1', () => {
    const tf = deriveTransform(fakeModel({ ParamBustY: -1 }))
    expect(tf.scaleY).toBeCloseTo(0.88); expect(tf.scaleX).toBeCloseTo(1.06)
  })
})
