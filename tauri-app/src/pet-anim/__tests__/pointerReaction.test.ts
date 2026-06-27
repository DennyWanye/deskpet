// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * pointerReaction.test.ts — TDD §4.7 (TC-PR-01..10)
 */
import { describe, expect, it } from 'vitest'
import { createPointerReactor } from '../pointerReaction'

describe('pointerReaction', () => {
  it('TC-PR-01 (P0) rest → onClick → pending_single, effect=null', () => {
    const r = createPointerReactor()
    const ctx0 = r.init()
    const r1 = r.onClick(ctx0, 100)
    expect(r1.ctx.state).toBe('pending_single')
    expect(r1.effect).toBeNull()
  })

  it('TC-PR-02 (P0) pending_single + threshold 后 tick → in_click_pulse, effect=click', () => {
    const r = createPointerReactor({ double_click_threshold_ms: 300 })
    let ctx = r.init()
    ctx = r.onClick(ctx, 100).ctx
    const r2 = r.tick(ctx, 100 + 301)
    expect(r2.ctx.state).toBe('in_click_pulse')
    expect(r2.effect).toBe('click')
  })

  it('TC-PR-03 (P0) pending_single + onClick (threshold 内) → in_double_pulse', () => {
    const r = createPointerReactor({ double_click_threshold_ms: 300 })
    let ctx = r.init()
    ctx = r.onClick(ctx, 100).ctx
    const r2 = r.onClick(ctx, 250)
    expect(r2.ctx.state).toBe('in_double_pulse')
    expect(r2.effect).toBe('double_click')
  })

  it('TC-PR-04 (P0) 301ms+ 两次 click → 两次 click (合计 2)', () => {
    const r = createPointerReactor({ double_click_threshold_ms: 300, click_pulse_ms: 50 })
    let ctx = r.init()
    const effects: string[] = []
    // First click + tick after threshold + tick after pulse to return to rest.
    ctx = r.onClick(ctx, 100).ctx
    const t1 = r.tick(ctx, 410)
    if (t1.effect) effects.push(t1.effect)
    ctx = t1.ctx
    const t1b = r.tick(ctx, 470) // past click_pulse_ms (50) → back to rest
    ctx = t1b.ctx
    expect(ctx.state).toBe('rest')
    // Second click + tick after threshold.
    ctx = r.onClick(ctx, 500).ctx
    const t2 = r.tick(ctx, 810)
    if (t2.effect) effects.push(t2.effect)
    expect(effects).toEqual(['click', 'click'])
  })

  it('TC-PR-05 (P0) hover_enter 与 hover_leave 顺序', () => {
    const r = createPointerReactor()
    let ctx = r.init()
    const e1 = r.onPointerEnter(ctx, 100)
    expect(e1.effect).toBe('hover_enter')
    expect(e1.ctx.is_hovering).toBe(true)
    ctx = e1.ctx
    const e2 = r.onPointerLeave(ctx, 200)
    expect(e2.effect).toBe('hover_leave')
    expect(e2.ctx.is_hovering).toBe(false)
  })

  it('TC-PR-06 (P0) hovering=true 期间 onClick → click 反应不打破 hovering', () => {
    const r = createPointerReactor({ double_click_threshold_ms: 100, click_pulse_ms: 50 })
    let ctx = r.init()
    ctx = r.onPointerEnter(ctx, 0).ctx
    expect(ctx.is_hovering).toBe(true)
    ctx = r.onClick(ctx, 50).ctx
    expect(ctx.is_hovering).toBe(true) // remains hovering
    // Advance past threshold → click_pulse
    ctx = r.tick(ctx, 200).ctx
    expect(ctx.is_hovering).toBe(true)
    expect(ctx.state).toBe('in_click_pulse')
    // Advance past pulse → back to rest, still hovering.
    ctx = r.tick(ctx, 300).ctx
    expect(ctx.state).toBe('rest')
    expect(ctx.is_hovering).toBe(true)
  })

  it('TC-PR-07 (P0) in_double_pulse 期间 onClick 被 ignore', () => {
    const r = createPointerReactor()
    let ctx = r.init()
    ctx = r.onClick(ctx, 100).ctx
    const r2 = r.onClick(ctx, 200) // promote to double
    ctx = r2.ctx
    expect(ctx.state).toBe('in_double_pulse')
    // Third click — swallowed.
    const r3 = r.onClick(ctx, 250)
    expect(r3.effect).toBeNull()
    expect(r3.ctx.state).toBe('in_double_pulse')
  })

  it('TC-PR-08 (P1) hover_debounce 内重复 enter 只 emit 一次', () => {
    const r = createPointerReactor({ hover_debounce_ms: 50 })
    let ctx = r.init()
    const e1 = r.onPointerEnter(ctx, 0)
    ctx = e1.ctx
    expect(e1.effect).toBe('hover_enter')
    const e2 = r.onPointerEnter(ctx, 10)
    expect(e2.effect).toBeNull() // already hovering
  })

  it('TC-PR-09 (P0) v3 hovering + in_click_pulse 期间 onPointerLeave → is_hovering=false + effect=hover_leave, state 不变', () => {
    const r = createPointerReactor({ double_click_threshold_ms: 100 })
    let ctx = r.init()
    ctx = r.onPointerEnter(ctx, 0).ctx
    ctx = r.onClick(ctx, 50).ctx
    ctx = r.tick(ctx, 200).ctx // pending → in_click_pulse
    expect(ctx.state).toBe('in_click_pulse')
    const leave = r.onPointerLeave(ctx, 220)
    expect(leave.effect).toBe('hover_leave')
    expect(leave.ctx.is_hovering).toBe(false)
    expect(leave.ctx.state).toBe('in_click_pulse')
  })

  it('TC-PR-10 (P0) v3 is_hovering=true 期间 onPointerEnter 重发 → effect=null', () => {
    const r = createPointerReactor()
    let ctx = r.init()
    ctx = r.onPointerEnter(ctx, 0).ctx
    const dup = r.onPointerEnter(ctx, 5)
    expect(dup.effect).toBeNull()
    expect(dup.ctx.is_hovering).toBe(true)
  })
})
