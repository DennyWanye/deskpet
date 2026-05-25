/**
 * AnimationOverlay v2 integration tests — TDD §4.13 (new TC-O-v2-* cases).
 *
 * Exercises every v2 setter (A1/B1/B2/B4) end-to-end through applyTo with
 * realistic Hiyori-ish parameter stubs.
 *
 * Strategy
 *   - Use a wider stub including v2-only params (ParamBodyAngleZ,
 *     ParamMouthForm, ParamHairFront, ParamBrowLY/RY).
 *   - Configure storage so v1 master + v2 master + the relevant per-FR flag
 *     are all on.
 *   - Drive applyTo for one frame after the setter, then assert the expected
 *     param writes appear in the stub log.
 *
 * AC-3.3 partial spot-check (full snapshot suite is in S3): v2_all=off path
 * is asserted in TC-OV2-10 — no v2 writes leak when the master flag is off.
 */
import { describe, expect, it } from 'vitest'
import { AnimationOverlay } from '../index'
import { FLAG_KEYS } from '../featureFlags'
import { fakeRng } from './_helpers'
import { makeStubCoreModel } from './_stubModel'

const HIYORI_V2_PARAMS = [
  // v1 core
  'ParamAngleX',
  'ParamAngleY',
  'ParamAngleZ',
  'ParamBodyAngleX',
  'ParamEyeLOpen',
  'ParamEyeROpen',
  'ParamEyeBallX',
  'ParamEyeBallY',
  'ParamMouthOpenY',
  // v2 extensions
  'ParamBodyAngleZ',
  'ParamMouthForm',
  'ParamHairFront',
  'ParamBrowLY',
  'ParamBrowRY',
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

/** Storage with v1 all=on (default) + v2_all=on + the specified v2 flag on. */
function v2Storage(extras: Record<string, string> = {}): Storage {
  return makeStorage({
    [FLAG_KEYS.v2_all]: 'on',
    ...extras,
  })
}

describe('AnimationOverlay v2', () => {
  it('TC-OV2-01 A1 setDragState(being_held) writes wobble ADD to ParamBodyAngleZ', () => {
    const storage = v2Storage({ [FLAG_KEYS.held]: 'on' })
    const overlay = new AnimationOverlay({ rng: fakeRng(1), storage })
    const stub = makeStubCoreModel(HIYORI_V2_PARAMS)

    overlay.setDragState('being_held', 0)
    // Advance to t = 75ms (1/4 period) where wobble ≈ amplitude (8°).
    overlay.applyTo(stub.coreModel, 75)

    const bodyAddEntries = stub.log.filter((l) => l.name === 'ParamBodyAngleZ' && l.op === 'add')
    expect(bodyAddEntries.length).toBeGreaterThan(0)
    const total = bodyAddEntries.reduce((acc, l) => acc + (l.v ?? 0), 0)
    expect(Math.abs(total)).toBeGreaterThan(7)
  })

  it('TC-OV2-02 A1 surprise pulse writes MouthForm + Brow + EyeOpen during initial window', () => {
    const storage = v2Storage({ [FLAG_KEYS.held]: 'on' })
    const overlay = new AnimationOverlay({ rng: fakeRng(1), storage })
    const stub = makeStubCoreModel(HIYORI_V2_PARAMS)

    overlay.setDragState('being_held', 0)
    overlay.applyTo(stub.coreModel, 0) // t=0 → surprise=1

    const snap = stub.snapshot()
    expect(snap.ParamMouthForm).toBeCloseTo(-0.5, 4)
    expect(snap.ParamBrowLY).toBeCloseTo(0.5, 4)
    expect(snap.ParamBrowRY).toBeCloseTo(0.5, 4)
    // EyeOpen pulse = 1 + 0.3 * 1 = 1.3 (then optionally MUL by blink mul ≈ 1).
    expect(snap.ParamEyeLOpen).toBeGreaterThan(1.2)
    expect(snap.ParamEyeROpen).toBeGreaterThan(1.2)
  })

  it('TC-OV2-03 A1 being_held blocks Perlin / gaze / saccade writes', () => {
    const storage = v2Storage({ [FLAG_KEYS.held]: 'on' })
    const overlay = new AnimationOverlay({ rng: fakeRng(1), storage })
    const stub = makeStubCoreModel(HIYORI_V2_PARAMS)

    overlay.setDragState('being_held', 0)
    overlay.applyTo(stub.coreModel, 1000)

    // Perlin would otherwise ADD to ParamAngleX/Y/BodyAngleX every frame.
    // While held, these adds must not appear.
    const headXAdds = stub.log.filter((l) => l.name === 'ParamAngleX' && l.op === 'add')
    expect(headXAdds.length).toBe(0)
    const bodyXAdds = stub.log.filter((l) => l.name === 'ParamBodyAngleX' && l.op === 'add')
    expect(bodyXAdds.length).toBe(0)
    // EyeBall should also not be written (gaze + saccade gated).
    const eyeBallSets = stub.log.filter((l) => l.name.startsWith('ParamEyeBall') && l.op === 'add')
    expect(eyeBallSets.length).toBe(0)
  })

  it('TC-OV2-04 B1 setUserInputActive(true) adds +3° to ParamAngleZ', () => {
    const storage = v2Storage({ [FLAG_KEYS.user_input]: 'on' })
    const overlay = new AnimationOverlay({ rng: fakeRng(1), storage })
    const stub = makeStubCoreModel(HIYORI_V2_PARAMS)

    overlay.setUserInputActive(true, 0)
    overlay.applyTo(stub.coreModel, 0)

    // ParamAngleZ is SET in step 6 to (base + transient + B1_tilt). base=0, transient=0.
    expect(stub.snapshot().ParamAngleZ).toBeCloseTo(3, 4)
  })

  it('TC-OV2-05 B1 multiplies ParamEyeLOpen/ROpen by 1.15 before blink mul', () => {
    const storage = v2Storage({ [FLAG_KEYS.user_input]: 'on' })
    const overlay = new AnimationOverlay({ rng: fakeRng(1), storage })
    overlay.setBlinkHz(0) // disable blink so the MUL by 1.15 is the only multiplier
    const stub = makeStubCoreModel(HIYORI_V2_PARAMS)
    const lIdx = stub.coreModel.getParameterIndex('ParamEyeLOpen')
    stub.coreModel.setParameterValueByIndex(lIdx, 1)
    const rIdx = stub.coreModel.getParameterIndex('ParamEyeROpen')
    stub.coreModel.setParameterValueByIndex(rIdx, 1)

    overlay.setUserInputActive(true, 0)
    overlay.applyTo(stub.coreModel, 0)

    const snap = stub.snapshot()
    expect(snap.ParamEyeLOpen).toBeCloseTo(1.15, 4)
    expect(snap.ParamEyeROpen).toBeCloseTo(1.15, 4)
  })

  it('TC-OV2-06 B1 writes oscillating ParamHairFront over time', () => {
    const storage = v2Storage({ [FLAG_KEYS.user_input]: 'on' })
    const overlay = new AnimationOverlay({ rng: fakeRng(1), storage })
    const stub = makeStubCoreModel(HIYORI_V2_PARAMS)
    overlay.setUserInputActive(true, 0)

    // Sample two phases of the 600ms oscillator
    overlay.applyTo(stub.coreModel, 150) // sin(π/2) = 1 → +0.2
    const peak = stub.snapshot().ParamHairFront
    expect(peak).toBeCloseTo(0.2, 4)

    const stub2 = makeStubCoreModel(HIYORI_V2_PARAMS)
    overlay.applyTo(stub2.coreModel, 450) // sin(3π/2) = -1 → -0.2
    const trough = stub2.snapshot().ParamHairFront
    expect(trough).toBeCloseTo(-0.2, 4)
  })

  it('TC-OV2-07 B2 setThinkingActive(true) adds +5° to ParamAngleX', () => {
    const storage = v2Storage({ [FLAG_KEYS.thinking]: 'on' })
    const overlay = new AnimationOverlay({ rng: fakeRng(1), storage })
    const stub = makeStubCoreModel(HIYORI_V2_PARAMS)

    overlay.setThinkingActive(true, 0)
    overlay.applyTo(stub.coreModel, 0)

    // Perlin amp at t=0 is 0; gaze/saccade do nothing yet; only B2 writes to AngleX.
    const headXAdds = stub.log.filter((l) => l.name === 'ParamAngleX' && l.op === 'add')
    const total = headXAdds.reduce((acc, l) => acc + (l.v ?? 0), 0)
    expect(total).toBeCloseTo(5, 4)
  })

  it('TC-OV2-08 B2 thinking sets brow up and adds EyeBallY up', () => {
    const storage = v2Storage({ [FLAG_KEYS.thinking]: 'on' })
    const overlay = new AnimationOverlay({ rng: fakeRng(1), storage })
    const stub = makeStubCoreModel(HIYORI_V2_PARAMS)

    overlay.setThinkingActive(true, 0)
    overlay.applyTo(stub.coreModel, 0)

    const snap = stub.snapshot()
    expect(snap.ParamBrowLY).toBeCloseTo(0.3, 4)
    expect(snap.ParamBrowRY).toBeCloseTo(0.3, 4)
    const eyeBallYAdds = stub.log.filter((l) => l.name === 'ParamEyeBallY' && l.op === 'add')
    const total = eyeBallYAdds.reduce((acc, l) => acc + (l.v ?? 0), 0)
    expect(total).toBeCloseTo(0.6, 4)
  })

  it('TC-OV2-09 B4 fadeMouthToZero overrides step-1 ParamMouthOpenY', () => {
    const storage = v2Storage({ [FLAG_KEYS.mouth_fade]: 'on' })
    const overlay = new AnimationOverlay({ rng: fakeRng(1), storage })
    const stub = makeStubCoreModel(HIYORI_V2_PARAMS)

    overlay.setMouthOpenY(0.6)
    overlay.fadeMouthToZero(200, 0)
    overlay.applyTo(stub.coreModel, 100) // mid-fade — ease-out(0.5)=0.875 → remaining 0.125 → 0.075

    const lastSet = [...stub.log].reverse().find((l) => l.name === 'ParamMouthOpenY' && l.op === 'set')
    expect(lastSet?.v).toBeCloseTo(0.6 * 0.125, 4)
  })

  it('TC-OV2-10 v2_all=off → setters become no-ops (AC-3.3 partial)', () => {
    const storage = makeStorage({ [FLAG_KEYS.v2_all]: 'off', [FLAG_KEYS.held]: 'on' })
    const overlay = new AnimationOverlay({ rng: fakeRng(1), storage })
    const stub = makeStubCoreModel(HIYORI_V2_PARAMS)

    overlay.setDragState('being_held', 0)
    overlay.setUserInputActive(true, 0)
    overlay.setThinkingActive(true, 0)
    overlay.fadeMouthToZero(200, 0)
    overlay.applyTo(stub.coreModel, 50)

    // None of the v2-only params should be touched.
    for (const p of ['ParamBodyAngleZ', 'ParamMouthForm', 'ParamHairFront', 'ParamBrowLY', 'ParamBrowRY']) {
      const entries = stub.log.filter((l) => l.name === p)
      expect(entries.length).toBe(0)
    }
  })

  it('TC-OV2-11 setDragState(idle) after held → spring_back decays wobble to zero', () => {
    const storage = v2Storage({ [FLAG_KEYS.held]: 'on' })
    const overlay = new AnimationOverlay({ rng: fakeRng(1), storage })
    const stub = makeStubCoreModel(HIYORI_V2_PARAMS)

    overlay.setDragState('being_held', 0)
    overlay.applyTo(stub.coreModel, 75) // peak wobble
    overlay.setDragState('idle', 75)

    // Right after release, mid-spring-back wobble is still non-zero.
    overlay.applyTo(stub.coreModel, 75 + 125) // 50% spring
    expect(overlay.getV2Debug().held_state).toBe('spring_back')
    expect(Math.abs(overlay.getV2Debug().held_wobble_deg)).toBeGreaterThan(0)

    // After spring_back_ms (250) elapses, FSM returns to idle.
    overlay.applyTo(stub.coreModel, 75 + 250)
    expect(overlay.getV2Debug().held_state).toBe('idle')
    expect(overlay.getV2Debug().held_wobble_deg).toBe(0)
  })

  it('TC-OV2-12 B2 thinking does NOT block Perlin (PRD §3 B2: only A1 held blocks step 2/3/4)', () => {
    const storage = v2Storage({ [FLAG_KEYS.thinking]: 'on' })
    const overlay = new AnimationOverlay({ rng: fakeRng(1), storage })
    const stub = makeStubCoreModel(HIYORI_V2_PARAMS)

    overlay.setThinkingActive(true, 0)
    // Run at t=1000 so Perlin's noise has a non-zero phase → ADD fires.
    overlay.applyTo(stub.coreModel, 1000)

    const angleYAdds = stub.log.filter((l) => l.name === 'ParamAngleY' && l.op === 'add')
    expect(angleYAdds.length).toBeGreaterThan(0)
  })
})
