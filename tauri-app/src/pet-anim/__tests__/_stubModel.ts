// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Stub Live2D coreModel for AnimationOverlay tests (TDD §5.2).
 *
 * `withAdd` lets tests pretend the native `addParameterValueByIndex` is
 * missing — exercises the get/set fallback path (TC-O-03). The log
 * captures every read/write/add so assertions can scan it for specific
 * parameter touches without resorting to spies.
 */
export interface StubLogEntry {
  name: string
  op: 'set' | 'add' | 'get'
  v?: number
  idx: number
}

export interface StubCoreModelHandle {
  log: StubLogEntry[]
  coreModel: {
    getParameterIndex(n: string): number
    getParameterValueByIndex(i: number): number
    setParameterValueByIndex(i: number, v: number): void
    addParameterValueByIndex?(i: number, v: number): void
  }
  snapshot(): Record<string, number>
}

export function makeStubCoreModel(
  params: string[],
  opts: { withAdd?: boolean } = {},
): StubCoreModelHandle {
  const idx = new Map(params.map((p, i) => [p, i]))
  const vals = new Array(params.length).fill(0)
  const log: StubLogEntry[] = []
  const core: StubCoreModelHandle['coreModel'] = {
    getParameterIndex: (n) => (idx.has(n) ? (idx.get(n) as number) : -1),
    getParameterValueByIndex: (i) => {
      log.push({ name: params[i] ?? '?', op: 'get', idx: i })
      return vals[i] ?? 0
    },
    setParameterValueByIndex: (i, v) => {
      log.push({ name: params[i] ?? '?', op: 'set', v, idx: i })
      vals[i] = v
    },
  }
  if (opts.withAdd !== false) {
    core.addParameterValueByIndex = (i, v) => {
      log.push({ name: params[i] ?? '?', op: 'add', v, idx: i })
      vals[i] += v
    }
  }
  return {
    log,
    coreModel: core,
    snapshot: () => Object.fromEntries(params.map((p, i) => [p, vals[i]])),
  }
}

/** Standard Hiyori-like parameter list. */
export const HIYORI_PARAMS = [
  'ParamAngleX',
  'ParamAngleY',
  'ParamAngleZ',
  'ParamBodyAngleX',
  'ParamEyeLOpen',
  'ParamEyeROpen',
  'ParamEyeBallX',
  'ParamEyeBallY',
  'ParamMouthOpenY',
]
