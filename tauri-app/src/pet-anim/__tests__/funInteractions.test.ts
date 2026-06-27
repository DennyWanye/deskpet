// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { describe, expect, it } from 'vitest'
import {
  createDragKinematicsCtx,
  dragKinematicsBegin,
  dragKinematicsUpdate,
  dragKinematicsSample,
  dragKinematicsEnd,
  createDragSpringBackCtx,
  dragSpringBackBegin,
  dragSpringBackSample,
  createTapBurstCtx,
  tapBurstAdd,
  regionAwareClassify,
  createLongPressCtx,
  longPressBegin,
  longPressSample,
  cursorProximityLean,
  createShyAwayCtx,
  shyAwayUpdate,
  createCircleDizzyCtx,
  circleDizzyUpdate,
  timeOfDayMood,
  createIdleFidgetCtx,
  idleFidgetMarkInteraction,
  idleFidgetMaybeTrigger,
  createRapidDoubleTapCtx,
  rapidDoubleTapAdd,
  rapidDoubleTapSample,
  clamp,
  easeOutCubic,
} from '../funInteractions'

describe('helpers', () => {
  it('clamp respects bounds + NaN', () => {
    expect(clamp(5, 0, 10)).toBe(5)
    expect(clamp(-3, 0, 10)).toBe(0)
    expect(clamp(99, 0, 10)).toBe(10)
    expect(clamp(NaN, 0, 10)).toBe(0)
  })
  it('easeOutCubic 0=0, 1=1, 0.5≈0.875', () => {
    expect(easeOutCubic(0)).toBe(0)
    expect(easeOutCubic(1)).toBe(1)
    expect(easeOutCubic(0.5)).toBeCloseTo(0.875, 3)
  })
})

describe('dragKinematics', () => {
  it('cold ctx outputs zero', () => {
    const ctx = createDragKinematicsCtx()
    expect(dragKinematicsSample(ctx)).toEqual({ squash_delta: 0, lean_delta_deg: 0, hair_trail_delta: 0 })
  })

  it('upward yank produces positive squash + leftward yank produces positive lean', () => {
    let ctx = createDragKinematicsCtx()
    ctx = dragKinematicsBegin(ctx, 100, 100, 1000)
    // Move up + left fast: 50px up in 25ms = -2000 px/s vy, 50px left = -2000 px/s vx
    ctx = dragKinematicsUpdate(ctx, 50, 50, 1025)
    const out = dragKinematicsSample(ctx)
    expect(out.squash_delta).toBeGreaterThan(0)
    expect(out.lean_delta_deg).toBeGreaterThan(0) // -vx / 250 = +ve
    expect(out.hair_trail_delta).toBeGreaterThan(0)
  })

  it('end() marks inactive — sample returns zero', () => {
    let ctx = createDragKinematicsCtx()
    ctx = dragKinematicsBegin(ctx, 0, 0, 1000)
    ctx = dragKinematicsUpdate(ctx, 10, 10, 1010)
    ctx = dragKinematicsEnd(ctx)
    expect(dragKinematicsSample(ctx)).toEqual({ squash_delta: 0, lean_delta_deg: 0, hair_trail_delta: 0 })
  })
})

describe('dragSpringBack', () => {
  it('cold returns done immediately', () => {
    const ctx = createDragSpringBackCtx()
    const s = dragSpringBackSample(ctx, 100)
    expect(s.done).toBe(true)
    expect(s.squash_delta).toBe(0)
  })

  it('mid-spring produces non-zero delta', () => {
    const ctx = dragSpringBackBegin(0.2, 1000)
    const mid = dragSpringBackSample(ctx, 1175) // t=0.5
    expect(Math.abs(mid.squash_delta)).toBeGreaterThan(0.001)
    expect(mid.done).toBe(false)
  })

  it('post-duration returns done=true zero delta', () => {
    const ctx = dragSpringBackBegin(0.2, 1000)
    const post = dragSpringBackSample(ctx, 2000)
    expect(post.done).toBe(true)
  })
})

describe('tapBurst', () => {
  it('1 tap = look_up', () => {
    const r = tapBurstAdd(createTapBurstCtx(), 1000)
    expect(r.count).toBe(1)
    expect(r.intensity).toBe('look_up')
  })

  it('3 taps within window = blush', () => {
    let c = createTapBurstCtx()
    c = tapBurstAdd(c, 1000).ctx
    c = tapBurstAdd(c, 1200).ctx
    const r = tapBurstAdd(c, 1400)
    expect(r.count).toBe(3)
    expect(r.intensity).toBe('blush')
  })

  it('5+ taps = annoyed', () => {
    let c = createTapBurstCtx()
    for (let i = 0; i < 4; i++) c = tapBurstAdd(c, 1000 + i * 100).ctx
    const r = tapBurstAdd(c, 1500)
    expect(r.count).toBe(5)
    expect(r.intensity).toBe('annoyed')
  })

  it('taps beyond window are dropped', () => {
    let c = createTapBurstCtx(500)
    c = tapBurstAdd(c, 1000).ctx
    c = tapBurstAdd(c, 1100).ctx
    const r = tapBurstAdd(c, 2000)
    expect(r.count).toBe(1) // only the 2000 one survives
    expect(r.intensity).toBe('look_up')
  })
})

describe('regionAwareClassify', () => {
  const frame = {
    left: 100, top: 200, width: 200, height: 400,
    face_center_y: 280, face_radius_css: 60,
  }
  it('top 20% → head', () => {
    expect(regionAwareClassify(200, 240, frame)).toBe('head')
  })
  it('20-45% → face', () => {
    expect(regionAwareClassify(200, 320, frame)).toBe('face')
  })
  it('45-90% → torso', () => {
    expect(regionAwareClassify(200, 450, frame)).toBe('torso')
  })
  it('>=90% → legs', () => {
    expect(regionAwareClassify(200, 580, frame)).toBe('legs')
  })
  it('outside → unknown', () => {
    expect(regionAwareClassify(50, 100, frame)).toBe('unknown')
    expect(regionAwareClassify(500, 700, frame)).toBe('unknown')
  })
})

describe('longPressPet', () => {
  it('not pressed → not petting', () => {
    const ctx = createLongPressCtx(600)
    expect(longPressSample(ctx, 1000)).toEqual({ petting: false, intensity: 0 })
  })

  it('below threshold → not petting', () => {
    const ctx = longPressBegin(createLongPressCtx(600), 1000)
    expect(longPressSample(ctx, 1500).petting).toBe(false) // 500ms < 600
  })

  it('above threshold → petting + intensity ramps', () => {
    const ctx = longPressBegin(createLongPressCtx(600), 1000)
    const s1 = longPressSample(ctx, 1700) // 100ms past threshold
    const s2 = longPressSample(ctx, 2700) // 1100ms past
    expect(s1.petting).toBe(true)
    expect(s2.petting).toBe(true)
    expect(s2.intensity).toBeGreaterThan(s1.intensity)
    expect(s2.intensity).toBeLessThanOrEqual(1)
  })
})

describe('cursorProximityLean', () => {
  it('far away → zero', () => {
    const r = cursorProximityLean(1000, 1000, 100, 100, 80)
    expect(r.lean_delta_deg).toBe(0)
  })

  it('cursor right of face → positive lean (toward cursor)', () => {
    const r = cursorProximityLean(140, 100, 100, 100, 80)
    expect(r.lean_delta_deg).toBeGreaterThan(0)
  })

  it('cursor below face → negative pitch (head looks down)', () => {
    const r = cursorProximityLean(100, 140, 100, 100, 80)
    expect(r.head_pitch_delta_deg).toBeLessThan(0)
  })
})

describe('shyAwayUpdate', () => {
  it('slow cursor → no trigger', () => {
    let ctx = createShyAwayCtx()
    let r = shyAwayUpdate(ctx, 100, 100, 100, 100, 50, 1000)
    ctx = r.ctx
    r = shyAwayUpdate(ctx, 105, 105, 100, 100, 50, 1100) // 7px in 100ms = 70 px/s
    expect(r.triggered).toBe(false)
  })

  it('fast cursor over face → triggered', () => {
    // Cursor needs to be within face_radius * 1.2 of face center to count
    // as "over face". Face at (100,100) radius 50 → over-face zone is 60px.
    // Start at (90, 100), move to (140, 100) — both inside the 60px zone,
    // 50px traveled in 25ms = 2000 px/s > 1500 threshold.
    let ctx = createShyAwayCtx()
    let r = shyAwayUpdate(ctx, 90, 100, 100, 100, 50, 1000)
    ctx = r.ctx
    r = shyAwayUpdate(ctx, 140, 100, 100, 100, 50, 1025)
    expect(r.triggered).toBe(true)
    expect(r.ctx.triggered_until_t).toBeGreaterThan(1025)
  })

  it('cooldown prevents re-trigger', () => {
    let ctx = createShyAwayCtx()
    let r = shyAwayUpdate(ctx, 90, 100, 100, 100, 50, 1000)
    ctx = r.ctx
    r = shyAwayUpdate(ctx, 140, 100, 100, 100, 50, 1025)
    ctx = r.ctx
    r = shyAwayUpdate(ctx, 90, 100, 100, 100, 50, 1100) // another fast move during cooldown
    expect(r.triggered).toBe(false)
  })
})

describe('circleDizzy', () => {
  it('no circle → no trigger', () => {
    let ctx = createCircleDizzyCtx()
    for (let i = 0; i < 10; i++) {
      const r = circleDizzyUpdate(ctx, 50 + i, 50, 50, 50, 30, 1000 + i * 50)
      ctx = r.ctx
      expect(r.triggered).toBe(false)
    }
  })

  it('two full circles in <1.5s → triggered', () => {
    let ctx = createCircleDizzyCtx()
    let trig = false
    const cx = 200, cy = 200, radius = 40
    // 80 samples covering 720° = 4π total angular travel within 1.2s.
    // Each step: angle = (i / 20) * Math.PI → i=20 → π, i=40 → 2π,
    // i=80 → 4π. Threshold is 4π so we should trigger near i≈80.
    for (let i = 0; i < 90; i++) {
      const angle = (i / 20) * Math.PI
      const x = cx + Math.cos(angle) * radius
      const y = cy + Math.sin(angle) * radius
      const r = circleDizzyUpdate(ctx, x, y, cx, cy, 30, 1000 + i * 15)
      ctx = r.ctx
      if (r.triggered) { trig = true; break }
    }
    expect(trig).toBe(true)
  })
})

describe('timeOfDayMood', () => {
  it('morning 7am → perky', () => {
    expect(timeOfDayMood(7).mood).toBe('perky')
  })
  it('noon 14 → normal', () => {
    expect(timeOfDayMood(14).mood).toBe('normal')
  })
  it('late night 23 → sleepy', () => {
    expect(timeOfDayMood(23).mood).toBe('sleepy')
    expect(timeOfDayMood(2).mood).toBe('sleepy')
  })
  it('NaN → normal fallback', () => {
    expect(timeOfDayMood(NaN).mood).toBe('normal')
  })
})

describe('idleFidget', () => {
  it('cold ctx without interaction → no trigger', () => {
    const ctx = createIdleFidgetCtx()
    const r = idleFidgetMaybeTrigger(ctx, 100_000, () => 0)
    expect(r.trigger).toBeNull()
  })

  it('after interaction + waiting past gap → trigger', () => {
    let ctx = createIdleFidgetCtx(1000, 1000) // fixed 1s gap
    ctx = idleFidgetMarkInteraction(ctx, 100)
    let r = idleFidgetMaybeTrigger(ctx, 200, () => 0)
    ctx = r.ctx
    expect(r.trigger).toBeNull() // not yet
    r = idleFidgetMaybeTrigger(ctx, 1200, () => 0)
    expect(r.trigger).toBe('yawn') // first choice with rand=0
  })

  it('mark interaction resets schedule', () => {
    let ctx = createIdleFidgetCtx(1000, 1000)
    ctx = idleFidgetMarkInteraction(ctx, 100)
    ctx = idleFidgetMaybeTrigger(ctx, 200, () => 0).ctx
    ctx = idleFidgetMarkInteraction(ctx, 500)
    expect(Number.isNaN(ctx.next_t)).toBe(true)
  })
})

describe('rapidDoubleTap', () => {
  it('first tap alone → not triggered', () => {
    const r = rapidDoubleTapAdd(createRapidDoubleTapCtx(), 1000)
    expect(r.triggered).toBe(false)
  })

  it('two taps within window → triggered', () => {
    let ctx = createRapidDoubleTapCtx(250)
    ctx = rapidDoubleTapAdd(ctx, 1000).ctx
    const r = rapidDoubleTapAdd(ctx, 1200)
    expect(r.triggered).toBe(true)
    expect(r.ctx.surprise_until_t).toBeGreaterThan(1200)
  })

  it('two taps past window → not triggered', () => {
    let ctx = createRapidDoubleTapCtx(250)
    ctx = rapidDoubleTapAdd(ctx, 1000).ctx
    const r = rapidDoubleTapAdd(ctx, 1500)
    expect(r.triggered).toBe(false)
  })

  it('sample factor decays linearly', () => {
    let ctx = createRapidDoubleTapCtx(250)
    ctx = rapidDoubleTapAdd(ctx, 1000).ctx
    ctx = rapidDoubleTapAdd(ctx, 1200).ctx
    const f1 = rapidDoubleTapSample(ctx, 1200).factor
    const f2 = rapidDoubleTapSample(ctx, 1500).factor
    expect(f1).toBeGreaterThan(f2)
    // surprise 持续 1100ms（2026-05-31 fun-ux 延长），1200+1100=2300 后归零。
    const f3 = rapidDoubleTapSample(ctx, 2400).factor
    expect(f3).toBe(0)
  })
})
