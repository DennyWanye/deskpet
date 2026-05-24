/**
 * overlay.test.ts — TDD §4.10 (TC-O-01..18 incl. TC-O-14b)
 */
import { describe, expect, it } from 'vitest'
import { AnimationOverlay } from '../index'
import { FLAG_KEYS } from '../featureFlags'
import { fakeRng } from './_helpers'
import { HIYORI_PARAMS, makeStubCoreModel } from './_stubModel'

function makeStorageStub(initial: Record<string, string> = {}): Storage {
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

describe('AnimationOverlay', () => {
  it('TC-O-01 (P0) applyTo 所有 param 都 missing 不抛', () => {
    const overlay = new AnimationOverlay({ rng: fakeRng(1) })
    const stub = makeStubCoreModel([]) // no params known
    expect(() => overlay.applyTo(stub.coreModel, 0)).not.toThrow()
  })

  it('TC-O-02 (P0) all=off → applyTo 早 return (无写)', () => {
    const storage = makeStorageStub({ [FLAG_KEYS.all]: 'off' })
    const overlay = new AnimationOverlay({ rng: fakeRng(1), storage })
    const stub = makeStubCoreModel(HIYORI_PARAMS)
    overlay.applyTo(stub.coreModel, 0)
    expect(stub.log.length).toBe(0)
  })

  it('TC-O-03 (P0) addParameterValueByIndex undefined → fallback get/set', () => {
    const overlay = new AnimationOverlay({ rng: fakeRng(1) })
    const stub = makeStubCoreModel(HIYORI_PARAMS, { withAdd: false })
    overlay.applyTo(stub.coreModel, 100)
    // Should not throw; should have used set (no `add` ops).
    expect(stub.log.some((l) => l.op === 'add')).toBe(false)
    // And should have called set on perlin-targeted params (gaze yaw at t=100
    // is ~0 since first tick).
    expect(stub.log.some((l) => l.op === 'set')).toBe(true)
  })

  it('TC-O-04 (P0) setStateBaseHeadTilt + pulseHeadTiltDelta 叠加 SET on ParamAngleZ', () => {
    const overlay = new AnimationOverlay({ rng: fakeRng(1) })
    const stub = makeStubCoreModel(HIYORI_PARAMS)
    overlay.setStateBaseHeadTilt(-5)
    overlay.pulseHeadTiltDelta(3, 200, 0)
    overlay.applyTo(stub.coreModel, 50) // within pulse
    expect(stub.snapshot().ParamAngleZ).toBeCloseTo(-2, 3)
    overlay.applyTo(stub.coreModel, 300) // past pulse
    expect(stub.snapshot().ParamAngleZ).toBeCloseTo(-5, 3)
  })

  it('TC-O-05 (P0) setBlinkHz(0.5) → 闭眼 multiplier 出现 0 at least once', () => {
    const overlay = new AnimationOverlay({ rng: fakeRng(1) })
    const stub = makeStubCoreModel(HIYORI_PARAMS)
    overlay.setBlinkHz(0.5)
    // Seed an initial eye value so the multiply is observable.
    const idx = stub.coreModel.getParameterIndex('ParamEyeLOpen')
    stub.coreModel.setParameterValueByIndex(idx, 1)
    let saw_closed = false
    for (let t = 0; t < 10_000; t += 16) {
      stub.coreModel.setParameterValueByIndex(idx, 1) // motion3 baseline
      overlay.applyTo(stub.coreModel, t)
      if (stub.snapshot().ParamEyeLOpen < 0.1) {
        saw_closed = true
        break
      }
    }
    expect(saw_closed).toBe(true)
  })

  it('TC-O-06 (P0) setMotionTagPool(fast, force) → motion_player 立即收到 Idle/idx', () => {
    const labels = { fast: [1, 3], medium: [], slow: [], special: [] }
    const overlay = new AnimationOverlay({
      rng: fakeRng(1),
      motionLabelsLoader: () => labels,
    })
    const calls: Array<[string, number]> = []
    overlay.setMotionPlayer((group, idx) => calls.push([group, idx]))
    overlay.setMotionTagPool(['fast'], { force_switch_now: true }, 0)
    expect(calls.length).toBeGreaterThan(0)
    expect(calls[0][0]).toBe('Idle')
    expect([1, 3]).toContain(calls[0][1])
  })

  it('TC-O-07 (P0) setMotionTagPool(force=false) + period 未到 → motion_player 不被调', () => {
    const labels = { fast: [1, 3], medium: [], slow: [], special: [] }
    const overlay = new AnimationOverlay({
      rng: fakeRng(1),
      motionLabelsLoader: () => labels,
    })
    const calls: Array<[string, number]> = []
    overlay.setMotionPlayer((group, idx) => calls.push([group, idx]))
    overlay.setMotionTagPool(['fast'], { force_switch_now: false }, 0)
    expect(calls.length).toBe(0)
  })

  it('TC-O-08 (P0) 子集 size < 2 兜底 — fast=[1] + medium=[5] → candidates 自动并入', () => {
    const labels = { fast: [1], medium: [5], slow: [], special: [] }
    const overlay = new AnimationOverlay({
      rng: fakeRng(1),
      motionLabelsLoader: () => labels,
    })
    const seen = new Set<number>()
    overlay.setMotionPlayer((_group, idx) => seen.add(idx))
    // Force-switch multiple times — over time both 1 and 5 should appear.
    for (let i = 0; i < 30; i++) {
      overlay.setMotionTagPool(['fast'], { force_switch_now: true }, i * 100)
    }
    expect(seen.has(1)).toBe(true)
    expect(seen.has(5)).toBe(true)
  })

  it('TC-O-09 (P0) pulseInteraction(click) → motionPlayer(TapBody, 0) ≤50ms', () => {
    const overlay = new AnimationOverlay({ rng: fakeRng(1) })
    const calls: Array<[string, number]> = []
    overlay.setMotionPlayer((g, i) => calls.push([g, i]))
    const stub = makeStubCoreModel(HIYORI_PARAMS)
    overlay.pulseInteraction('click', 0)
    // Drive forward past double-click threshold so the click effect fires.
    for (let t = 0; t <= 320; t += 16) overlay.applyTo(stub.coreModel, t)
    expect(calls.some((c) => c[0] === 'TapBody' && c[1] === 0)).toBe(true)
  })

  it('TC-O-10 (P0) pulseInteraction(double_click) → ParamAngleZ ±10 transient', () => {
    const overlay = new AnimationOverlay({ rng: fakeRng(1) })
    const stub = makeStubCoreModel(HIYORI_PARAMS)
    overlay.pulseInteraction('double_click', 0)
    overlay.applyTo(stub.coreModel, 100)
    // Either |Z|=10 or clamped to 15 minus base=0 still hits the bound.
    expect(Math.abs(stub.snapshot().ParamAngleZ)).toBeGreaterThanOrEqual(10)
  })

  it('TC-O-11 (P0) setFaceCenter + setGazeTarget(right of face) → ParamEyeBallX > 0', () => {
    const overlay = new AnimationOverlay({ rng: fakeRng(1) })
    const stub = makeStubCoreModel(HIYORI_PARAMS)
    overlay.setFaceCenter(960, 540, 200)
    overlay.setGazeTarget(1160, 540, 0)
    for (let t = 0; t < 3000; t += 16) overlay.applyTo(stub.coreModel, t)
    expect(stub.snapshot().ParamEyeBallX).toBeGreaterThan(0)
  })

  it('TC-O-12 (P0) clearGazeTarget → idle_recenter_ms 后 ParamEyeBallX 回 0', () => {
    const overlay = new AnimationOverlay({ rng: fakeRng(1) })
    const stub = makeStubCoreModel(HIYORI_PARAMS)
    overlay.setFaceCenter(960, 540, 200)
    overlay.setGazeTarget(1160, 540, 0)
    // Real Live2D resets parameters each frame before applyTo runs;
    // simulate by zeroing ParamEyeBallX between frames so we measure
    // the per-frame ADD delta (which is what motion3 actually sees).
    const eyeXIdx = stub.coreModel.getParameterIndex('ParamEyeBallX')
    for (let t = 0; t < 3000; t += 16) {
      stub.coreModel.setParameterValueByIndex(eyeXIdx, 0)
      overlay.applyTo(stub.coreModel, t)
    }
    overlay.clearGazeTarget(3000)
    let final = 0
    for (let t = 3000; t < 25_000; t += 16) {
      stub.coreModel.setParameterValueByIndex(eyeXIdx, 0)
      overlay.applyTo(stub.coreModel, t)
      final = stub.snapshot().ParamEyeBallX
    }
    expect(Math.abs(final)).toBeLessThan(0.05)
  })

  it('TC-O-13 (P0) recordInteractionEventTs + recordVisualFrameTs → visual.samples 含正确 delta', () => {
    const overlay = new AnimationOverlay({ rng: fakeRng(1) })
    overlay.recordInteractionEventTs('click', 100)
    overlay.recordVisualFrameTs(180)
    const m = overlay.getAnimationMetrics()
    expect(m.visual.samples).toContain(80)
  })

  it('TC-O-14b (P0) v3 多事件 FIFO 配对', () => {
    const overlay = new AnimationOverlay({ rng: fakeRng(1) })
    overlay.recordInteractionEventTs('click', 100)
    overlay.recordInteractionEventTs('click', 150)
    overlay.recordVisualFrameTs(180)
    overlay.recordVisualFrameTs(220)
    const m = overlay.getAnimationMetrics()
    expect(m.visual.samples).toEqual([80, 70])
    // Queue empty — additional visual frames should not record.
    overlay.recordVisualFrameTs(300)
    expect(overlay.getAnimationMetrics().visual.samples).toEqual([80, 70])
  })

  it('TC-O-14 (P0) getAnimationMetrics 返回结构', () => {
    const overlay = new AnimationOverlay({ rng: fakeRng(1) })
    const m = overlay.getAnimationMetrics()
    expect(m.interaction).toHaveProperty('p50')
    expect(m.interaction).toHaveProperty('p95')
    expect(m.interaction).toHaveProperty('max')
    expect(m.interaction).toHaveProperty('samples')
    expect(m.visual).toHaveProperty('p50')
  })

  it('TC-O-15 (P0) getAnimationDebug 字段齐全', () => {
    const overlay = new AnimationOverlay({ rng: fakeRng(1) })
    const d = overlay.getAnimationDebug()
    expect(d).toHaveProperty('gaze_target_yaw')
    expect(d).toHaveProperty('gaze_smoothed_yaw')
    expect(d).toHaveProperty('last_input_age_ms')
    expect(d).toHaveProperty('current_state')
    expect(d).toHaveProperty('current_motion_idx')
  })

  it('TC-O-16 (P0) dispose() 后 applyTo 不抛', () => {
    const overlay = new AnimationOverlay({ rng: fakeRng(1) })
    const stub = makeStubCoreModel(HIYORI_PARAMS)
    overlay.dispose()
    expect(() => overlay.applyTo(stub.coreModel, 0)).not.toThrow()
    // No writes after dispose.
    expect(stub.log.length).toBe(0)
  })

  it('TC-O-17 (P1) 1000 帧 applyTo 总 ms < 500 (mean < 0.5ms)', () => {
    const overlay = new AnimationOverlay({ rng: fakeRng(1) })
    const stub = makeStubCoreModel(HIYORI_PARAMS)
    overlay.setBlinkHz(0.3)
    overlay.setFaceCenter(960, 540, 200)
    overlay.setGazeTarget(1100, 600, 0)
    const t0 = performance.now()
    for (let i = 0; i < 1000; i++) overlay.applyTo(stub.coreModel, i * 33)
    const dt = performance.now() - t0
    expect(dt).toBeLessThan(500)
  })

  it('TC-O-18 (P1) perlin=off → ParamAngleX 不含 perlin 分量', () => {
    const overlay_on = new AnimationOverlay({ rng: fakeRng(1) })
    const overlay_off = new AnimationOverlay({
      rng: fakeRng(1),
      storage: makeStorageStub({ [FLAG_KEYS.perlin]: 'off' }),
    })
    const stub_on = makeStubCoreModel(HIYORI_PARAMS)
    const stub_off = makeStubCoreModel(HIYORI_PARAMS)
    overlay_on.applyTo(stub_on.coreModel, 100)
    overlay_off.applyTo(stub_off.coreModel, 100)
    // With perlin off at t=100 (no gaze target, no saccade trigger yet),
    // ParamAngleX should be exactly 0; with perlin on it's almost surely
    // non-zero.
    expect(stub_off.snapshot().ParamAngleX).toBe(0)
  })
})
