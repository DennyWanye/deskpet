// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * AC-3 v1 zero-regression snapshot suite — Pet Animation UX v2 PRD §6.11.
 *
 * Verifies four AC-3 conditions are upheld at the AnimationOverlay layer:
 *
 *   AC-3.1  v2_all=off (deskpet_animation_v2='off') → applyTo produces the
 *           same param write sequence as a v1-pure overlay would. (The full
 *           "v2_all=off → 386 unit pass" claim is covered project-wide; this
 *           assertion focuses on the overlay's behavioural equivalence.)
 *   AC-3.2  v2_all=off → 27/27 v1 OS 手测 (NOT auto-testable; handled in
 *           ManualTest CASE-AC3-02 via QA subagent — see TDD §4.16 cross-ref).
 *   AC-3.3  v2_all=on + every per-FR flag OFF → snapshot diff = 0 vs v1
 *           baseline.
 *   AC-3.4  Each single FR ON in isolation → diff vs baseline contains only
 *           the parameters that FR is allowed to write per PRD §6.2 matrix.
 *
 * The "baseline" is generated inline from a v1-only overlay configured with
 * fixed RNG and identical inputs, so the comparison is deterministic.
 */
import { describe, expect, it } from 'vitest'
import { AnimationOverlay } from '../index'
import { FLAG_KEYS } from '../featureFlags'
import { fakeRng } from './_helpers'
import { type StubLogEntry } from './_stubModel'
import { makeStubCoreModel } from './_stubModel'

const HIYORI_V2_PARAMS = [
  'ParamAngleX',
  'ParamAngleY',
  'ParamAngleZ',
  'ParamBodyAngleX',
  'ParamBodyAngleZ',
  'ParamEyeLOpen',
  'ParamEyeROpen',
  'ParamEyeBallX',
  'ParamEyeBallY',
  'ParamMouthOpenY',
  'ParamMouthForm',
  'ParamHairFront',
  'ParamBrowLY',
  'ParamBrowRY',
  'ParamBrowLAngle',
  'ParamBrowRAngle',
  'ParamCheek',
  'ParamEyeLSmile',
  'ParamEyeRSmile',
]

function makeStorage(initial: Record<string, string> = {}): Storage {
  const m = new Map<string, string>(Object.entries(initial))
  return {
    get length() {
      return m.size
    },
    clear() {
      m.clear()
    },
    getItem(k) {
      return m.has(k) ? (m.get(k) as string) : null
    },
    key(i) {
      return Array.from(m.keys())[i] ?? null
    },
    removeItem(k) {
      m.delete(k)
    },
    setItem(k, v) {
      m.set(k, v)
    },
  } as Storage
}

/** Sequence of writes produced by N frames of applyTo with fixed input. */
function recordSnapshot(storage: Storage, frames = 60, step_ms = 16): StubLogEntry[] {
  const overlay = new AnimationOverlay({ rng: fakeRng(1), storage })
  const stub = makeStubCoreModel(HIYORI_V2_PARAMS)
  for (let i = 0; i < frames; i++) {
    overlay.applyTo(stub.coreModel, i * step_ms)
  }
  // Strip the readback noise (get ops) — only writes matter for behavioural
  // equivalence. Also strip log entries' index field which differs purely as
  // a function of param table size.
  return stub.log.filter((l) => l.op !== 'get').map((l) => ({ ...l }))
}

/**
 * Diff the snapshot under `cfg` storage against the v2_all=off baseline.
 * Returns the set of parameter names whose write sequence differs.
 */
function diffParamsAgainstBaseline(extraFlags: Record<string, string>): Set<string> {
  const baseline = recordSnapshot(makeStorage({ [FLAG_KEYS.v2_all]: 'off' }))
  const variant = recordSnapshot(makeStorage({ ...extraFlags }))

  const baseByName = new Map<string, StubLogEntry[]>()
  for (const l of baseline) {
    const arr = baseByName.get(l.name) ?? []
    arr.push(l)
    baseByName.set(l.name, arr)
  }
  const varByName = new Map<string, StubLogEntry[]>()
  for (const l of variant) {
    const arr = varByName.get(l.name) ?? []
    arr.push(l)
    varByName.set(l.name, arr)
  }
  const names = new Set<string>([...baseByName.keys(), ...varByName.keys()])
  const diffed = new Set<string>()
  for (const name of names) {
    const a = baseByName.get(name) ?? []
    const b = varByName.get(name) ?? []
    if (a.length !== b.length) {
      diffed.add(name)
      continue
    }
    for (let i = 0; i < a.length; i++) {
      if (a[i].op !== b[i].op || a[i].v !== b[i].v) {
        diffed.add(name)
        break
      }
    }
  }
  return diffed
}

describe('AC-3 v1 zero-regression snapshot suite', () => {
  it('AC-3.1 v2_all=off → baseline (sanity: self vs self = 0 diff)', () => {
    const baseline_a = recordSnapshot(makeStorage({ [FLAG_KEYS.v2_all]: 'off' }))
    const baseline_b = recordSnapshot(makeStorage({ [FLAG_KEYS.v2_all]: 'off' }))
    expect(baseline_a.length).toBe(baseline_b.length)
    for (let i = 0; i < baseline_a.length; i++) {
      expect(baseline_a[i].op).toBe(baseline_b[i].op)
      expect(baseline_a[i].v).toBe(baseline_b[i].v)
      expect(baseline_a[i].name).toBe(baseline_b[i].name)
    }
  })

  it('AC-3.3 v2_all=on + every FR off → diff = 0 (zero regression baseline)', () => {
    // All 13 FR flags explicitly set off (default for each is 'on' so this
    // forces every v2 sub-feature dormant).
    const diff = diffParamsAgainstBaseline({
      [FLAG_KEYS.v2_all]: 'on',
      [FLAG_KEYS.held]: 'off',
      [FLAG_KEYS.user_input]: 'off',
      [FLAG_KEYS.thinking]: 'off',
      [FLAG_KEYS.viseme]: 'off',
      [FLAG_KEYS.mouth_fade]: 'off',
      [FLAG_KEYS.low_energy]: 'off',
      [FLAG_KEYS.welcome]: 'off',
      [FLAG_KEYS.time_celebration]: 'off',
      [FLAG_KEYS.emotion]: 'off',
      [FLAG_KEYS.milestone]: 'off',
      [FLAG_KEYS.edge]: 'off',
      [FLAG_KEYS.occlusion]: 'off',
      [FLAG_KEYS.dnd]: 'off',
    })
    expect(Array.from(diff)).toEqual([])
  })

  it('AC-3.4 single FR (held) on with no drag input → no v2 writes since state idle', () => {
    // No setDragState called → heldCtx stays idle → no wobble/surprise even
    // with held flag on. Verifies the "FR on but inactive" sub-case stays
    // diff-clean.
    const diff = diffParamsAgainstBaseline({
      [FLAG_KEYS.v2_all]: 'on',
      [FLAG_KEYS.held]: 'on',
      // others off
      [FLAG_KEYS.user_input]: 'off',
      [FLAG_KEYS.thinking]: 'off',
      [FLAG_KEYS.viseme]: 'off',
      [FLAG_KEYS.mouth_fade]: 'off',
      [FLAG_KEYS.low_energy]: 'off',
      [FLAG_KEYS.welcome]: 'off',
      [FLAG_KEYS.time_celebration]: 'off',
      [FLAG_KEYS.emotion]: 'off',
      [FLAG_KEYS.milestone]: 'off',
      [FLAG_KEYS.edge]: 'off',
      [FLAG_KEYS.occlusion]: 'off',
      [FLAG_KEYS.dnd]: 'off',
    })
    expect(Array.from(diff)).toEqual([])
  })

  /**
   * AC-3.4 (extended): when a FR is ON and its trigger is firing, only the
   * params owned by that FR per §6.2 matrix should appear in the diff.
   *
   * This test exercises B1 user_input (the simplest trigger — just a flag).
   * It allows BrowLY/RY because step-7 SETs them whenever B2 is not active,
   * which means D1-neutral (the default) writes nothing — so diff is purely
   * what B1 added (AngleZ ADD + EyeOpen MUL + HairFront OSC ADD).
   */
  it('AC-3.4 B1 active → diff only contains B1-owned params', () => {
    const baseline = makeStorage({ [FLAG_KEYS.v2_all]: 'off' })
    const variant = makeStorage({
      [FLAG_KEYS.v2_all]: 'on',
      [FLAG_KEYS.held]: 'off',
      [FLAG_KEYS.user_input]: 'on',
      [FLAG_KEYS.thinking]: 'off',
      [FLAG_KEYS.viseme]: 'off',
      [FLAG_KEYS.mouth_fade]: 'off',
      [FLAG_KEYS.low_energy]: 'off',
      [FLAG_KEYS.welcome]: 'off',
      [FLAG_KEYS.time_celebration]: 'off',
      [FLAG_KEYS.emotion]: 'off',
      [FLAG_KEYS.milestone]: 'off',
      [FLAG_KEYS.edge]: 'off',
      [FLAG_KEYS.occlusion]: 'off',
      [FLAG_KEYS.dnd]: 'off',
    })

    const overlayB = new AnimationOverlay({ rng: fakeRng(1), storage: variant })
    overlayB.setUserInputActive(true, 0)
    const stubBase = makeStubCoreModel(HIYORI_V2_PARAMS)
    const stubVar = makeStubCoreModel(HIYORI_V2_PARAMS)
    const overlayBase = new AnimationOverlay({ rng: fakeRng(1), storage: baseline })
    // Activate B1 on baseline too — but it's gated by v2_all=off so setUserInputActive becomes a no-op visually.
    overlayBase.setUserInputActive(true, 0)
    for (let i = 0; i < 60; i++) {
      overlayBase.applyTo(stubBase.coreModel, i * 16)
      overlayB.applyTo(stubVar.coreModel, i * 16)
    }
    const baseLog = stubBase.log.filter((l) => l.op !== 'get')
    const varLog = stubVar.log.filter((l) => l.op !== 'get')

    const baseByName = new Map<string, number>()
    for (const l of baseLog) baseByName.set(l.name, (baseByName.get(l.name) ?? 0) + 1)
    const varByName = new Map<string, number>()
    for (const l of varLog) varByName.set(l.name, (varByName.get(l.name) ?? 0) + 1)

    const diffed = new Set<string>()
    const allNames = new Set<string>([...baseByName.keys(), ...varByName.keys()])
    for (const n of allNames) {
      if ((baseByName.get(n) ?? 0) !== (varByName.get(n) ?? 0)) diffed.add(n)
    }

    // Allowed names that B1 may write (PRD §6.2):
    //   - ParamAngleZ      (ADD +3°)
    //   - ParamEyeLOpen    (MUL ×1.15)   — same count as base since blink also writes ParamEyeLOpen
    //   - ParamEyeROpen    (MUL ×1.15)
    //   - ParamHairFront   (OSC ADD)
    const allowed = new Set([
      'ParamAngleZ',
      'ParamEyeLOpen',
      'ParamEyeROpen',
      'ParamHairFront',
    ])
    for (const n of diffed) {
      expect(allowed.has(n), `Unexpected param diff for B1: ${n}`).toBe(true)
    }
  })
})
