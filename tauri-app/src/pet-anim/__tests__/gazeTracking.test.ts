/**
 * gazeTracking.test.ts — TDD §4.4 (TC-G-01..08)
 */
import { describe, expect, it } from 'vitest'
import { createGazeTracker } from '../gazeTracking'

const FACE_CX = 960
const FACE_CY = 540
const FACE_RADIUS = 200

function makeTracker() {
  const g = createGazeTracker()
  let state = g.init()
  state = g.setFaceFrame(state, FACE_CX, FACE_CY, FACE_RADIUS)
  return { g, state }
}

describe('gazeTracking', () => {
  it('TC-G-01 (P0) target=(face_center) → smoothed_yaw=0', () => {
    const { g, state: s0 } = makeTracker()
    let state = g.setTarget(s0, FACE_CX, FACE_CY, 0)
    for (let t = 0; t < 5000; t += 16) {
      state = g.tick(state, t).state
    }
    expect(state.smoothed_yaw_deg).toBeCloseTo(0, 4)
  })

  it('TC-G-02 (P0) target 远超 ±20° → smoothed clamp 在 ±20°', () => {
    const { g, state: s0 } = makeTracker()
    let state = g.setTarget(s0, FACE_CX + 100_000, FACE_CY, 0)
    for (let t = 0; t < 5000; t += 16) state = g.tick(state, t).state
    expect(state.smoothed_yaw_deg).toBeGreaterThan(19)
    expect(state.smoothed_yaw_deg).toBeLessThanOrEqual(20.01)
  })

  it('TC-G-03 (P0) 死区：|Δtarget| < deadzone → target snap 回 prev_smoothed', () => {
    const { g, state: s0 } = makeTracker()
    // Push smoothed up to 10°.
    let state = g.setTarget(s0, FACE_CX + 1000, FACE_CY, 0)
    for (let t = 0; t < 5000; t += 16) state = g.tick(state, t).state
    const before = state.smoothed_yaw_deg
    // Now set a target that's only 2° away from prev_smoothed → deadzone snaps.
    // 2° corresponds to dx ≈ tan(2°)*200 ≈ 7 px from where we are. Approximate
    // by re-setting current pointer at slight offset.
    const nudge = g.setTarget(state, FACE_CX + 1004, FACE_CY, 5000)
    expect(nudge.target_yaw_deg).toBe(before)
  })

  it('TC-G-04 (P0) 阶跃 0→20°, 10 次 tick 后 smoothed > 14°', () => {
    // Use a tracker without deadzone so 10 tick convergence is monotonic.
    const g = createGazeTracker({ deadzone_deg: 0 })
    let state = g.init()
    state = g.setFaceFrame(state, FACE_CX, FACE_CY, FACE_RADIUS)
    state = g.setTarget(state, FACE_CX + 1_000_000, FACE_CY, 0) // clamp to +20°
    for (let i = 0; i < 10; i++) state = g.tick(state, i * 16).state
    expect(state.smoothed_yaw_deg).toBeGreaterThan(14)
  })

  it('TC-G-05 (P0) clearTarget 后 idle_recenter_ms 内 smoothed → 0', () => {
    const { g, state: s0 } = makeTracker()
    let state = g.setTarget(s0, FACE_CX + 1000, FACE_CY, 0)
    for (let t = 0; t < 5000; t += 16) state = g.tick(state, t).state
    expect(state.smoothed_yaw_deg).toBeGreaterThan(15)
    state = g.clearTarget(state, 5000)
    // Walk forward past idle_recenter_ms (10 s default).
    for (let t = 5000; t < 25_000; t += 16) state = g.tick(state, t).state
    expect(state.smoothed_yaw_deg).toBeCloseTo(0, 1)
  })

  it('TC-G-06 (P0) head_yaw = eye_yaw_norm × head_follow_ratio × yaw_max_deg ratio', () => {
    const { g, state: s0 } = makeTracker()
    let state = g.setTarget(s0, FACE_CX + 200, FACE_CY, 0)
    let last_head = 0
    let last_eye_norm = 0
    for (let t = 0; t < 5000; t += 16) {
      const r = g.tick(state, t)
      state = r.state
      last_head = r.head_yaw_deg
      last_eye_norm = r.eye_yaw_norm
    }
    // head_yaw_deg = smoothed_yaw_deg * 0.4
    // eye_yaw_norm = smoothed_yaw_deg / 20 * 1.0
    // ratio head/eye_norm = 0.4 * 20 = 8
    if (Math.abs(last_eye_norm) > 1e-3) {
      expect(last_head / last_eye_norm).toBeCloseTo(0.4 * 20, 3)
    }
  })

  it('TC-G-07 (P0) 负 clientX (副屏在左) → smoothed_yaw < 0, 不为 NaN', () => {
    const { g, state: s0 } = makeTracker()
    let state = g.setTarget(s0, -300, FACE_CY, 0)
    for (let t = 0; t < 5000; t += 16) state = g.tick(state, t).state
    expect(Number.isNaN(state.smoothed_yaw_deg)).toBe(false)
    expect(state.smoothed_yaw_deg).toBeLessThan(0)
  })

  it('TC-G-08 (P1) setFaceFrame 改 face_radius=400 后, 相同 clientX 产生角度减半', () => {
    const g = createGazeTracker({ deadzone_deg: 0 })
    let s1 = g.init()
    s1 = g.setFaceFrame(s1, FACE_CX, FACE_CY, 200)
    // 20 px offset → 20/200 = 0.1 → atan ≈ 5.71° (still linear region).
    const small = g.setTarget(s1, FACE_CX + 20, FACE_CY, 0)
    const small_tgt = small.target_yaw_deg

    let s2 = g.init()
    s2 = g.setFaceFrame(s2, FACE_CX, FACE_CY, 400)
    // 20 px / 400 = 0.05 → atan ≈ 2.86°. Half-relation holds to ~1%.
    const big = g.setTarget(s2, FACE_CX + 20, FACE_CY, 0)
    const big_tgt = big.target_yaw_deg
    expect(big_tgt).toBeCloseTo(small_tgt / 2, 1)
  })
})
