// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import type { CoreModelLike } from '../pet-engine'

export interface PetTransform {
  rotateDeg: number
  offsetX: number
  offsetY: number
  scaleX: number
  scaleY: number
}

const clamp = (v: number, lo: number, hi: number): number => (v < lo ? lo : v > hi ? hi : v)

function read(model: CoreModelLike, name: string): number {
  const idx = model.getParameterIndex(name)
  if (idx < 0) return 0
  const v = model.getParameterValueByIndex(idx)
  return Number.isFinite(v) ? v : 0
}

export function deriveTransform(model: CoreModelLike): PetTransform {
  const angleZ = read(model, 'ParamAngleZ')
  const bodyAngleZ = read(model, 'ParamBodyAngleZ')
  const bodyAngleX = read(model, 'ParamBodyAngleX')
  const bustY = clamp(read(model, 'ParamBustY'), -1, 1)
  return {
    rotateDeg: clamp(angleZ + bodyAngleZ, -25, 25),
    offsetX: clamp(bodyAngleX, -30, 30) * 0.5,
    offsetY: 0,
    scaleY: 1 + bustY * 0.12,
    scaleX: 1 - bustY * 0.06,
  }
}
