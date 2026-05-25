/**
 * Unit tests for userInputObserver — B1 (TDD §4.2).
 *
 * Cases:
 *   TC-B1-01  init → all flags false
 *   TC-B1-02  focus + keydown → active=true; onChange fired with (true, now_t)
 *   TC-B1-03  tick after stop_after_idle_ms past last keydown → active=false; onChange fired (false)
 *   TC-B1-04  blur immediately drops active
 *   TC-B1-05  unfocused keydown does not become active
 *   TC-B1-06  IME composition: compositionstart blocks keydowns; compositionend commits as input
 *   TC-B1-07  Non-finite now_t is silently no-op (NFR-6 defence)
 *   TC-B1-08  onChange callback exception is swallowed; FSM still advances
 */
import { describe, it, expect, vi } from 'vitest'
import { createUserInputObserver } from '../userInputObserver'

describe('userInputObserver — B1', () => {
  it('TC-B1-01 init() → idle state', () => {
    const obs = createUserInputObserver()
    const s = obs.init()
    expect(s.focused).toBe(false)
    expect(s.composing).toBe(false)
    expect(s.active).toBe(false)
    expect(s.last_input_t).toBe(-Infinity)
  })

  it('TC-B1-02 focus + keydown → active=true; onChange fired', () => {
    const onChange = vi.fn()
    const obs = createUserInputObserver({}, onChange)
    let s = obs.init()
    s = obs.onFocus(s, 100)
    expect(s.active).toBe(false) // focus alone doesn't activate
    expect(onChange).not.toHaveBeenCalled()

    s = obs.onKeydown(s, 110)
    expect(s.active).toBe(true)
    expect(onChange).toHaveBeenCalledWith(true, 110)
  })

  it('TC-B1-03 tick after stop_after_idle_ms drops active', () => {
    const onChange = vi.fn()
    const obs = createUserInputObserver({ stop_after_idle_ms: 1500 }, onChange)
    let s = obs.init()
    s = obs.onFocus(s, 0)
    s = obs.onKeydown(s, 100)
    expect(s.active).toBe(true)

    // Still inside window
    s = obs.tick(s, 100 + 1499)
    expect(s.active).toBe(true)

    // Just past window
    s = obs.tick(s, 100 + 1500)
    expect(s.active).toBe(false)
    expect(onChange).toHaveBeenLastCalledWith(false, 100 + 1500)
  })

  it('TC-B1-04 blur immediately drops active even mid-window', () => {
    const obs = createUserInputObserver({ stop_after_idle_ms: 1500 })
    let s = obs.init()
    s = obs.onFocus(s, 0)
    s = obs.onKeydown(s, 100)
    expect(s.active).toBe(true)
    s = obs.onBlur(s, 150)
    expect(s.focused).toBe(false)
    expect(s.active).toBe(false)
  })

  it('TC-B1-05 unfocused keydown does not activate', () => {
    const onChange = vi.fn()
    const obs = createUserInputObserver({}, onChange)
    let s = obs.init()
    s = obs.onKeydown(s, 50)
    expect(s.active).toBe(false)
    expect(onChange).not.toHaveBeenCalled()
  })

  it('TC-B1-06 IME composition: keydown during composition does not activate; composend commits', () => {
    const onChange = vi.fn()
    const obs = createUserInputObserver({ ime_aware: true, stop_after_idle_ms: 1500 }, onChange)
    let s = obs.init()
    s = obs.onFocus(s, 0)

    // 用户按下 pinyin 字母 → compositionstart 之前可能有 1 个 keydown 触发 active
    // 我们模拟"先 compositionstart 再 keydown"
    s = obs.onCompositionStart(s, 10)
    expect(s.composing).toBe(true)
    expect(s.active).toBe(false)

    // 输入 "ni hao" 期间的 keydowns
    s = obs.onKeydown(s, 20)
    s = obs.onKeydown(s, 30)
    s = obs.onKeydown(s, 40)
    expect(s.active).toBe(false) // 全部被 IME 屏蔽
    expect(s.last_input_t).toBe(-Infinity)

    // 选定候选词 → compositionend
    s = obs.onCompositionEnd(s, 50)
    expect(s.composing).toBe(false)
    expect(s.active).toBe(true) // commit 触发 active
    expect(s.last_input_t).toBe(50)

    // 验证 onChange 仅在状态切变时触发：一次 true（在 compositionend）
    const trueCalls = onChange.mock.calls.filter((c) => c[0] === true)
    expect(trueCalls.length).toBe(1)
    expect(trueCalls[0][1]).toBe(50)
  })

  it('TC-B1-06b IME disabled (ime_aware=false) → keydowns count even during composition', () => {
    const obs = createUserInputObserver({ ime_aware: false })
    let s = obs.init()
    s = obs.onFocus(s, 0)
    s = obs.onCompositionStart(s, 10)
    s = obs.onKeydown(s, 20)
    expect(s.active).toBe(true)
    expect(s.last_input_t).toBe(20)
  })

  it('TC-B1-07 non-finite now_t → safe no-op', () => {
    const obs = createUserInputObserver()
    const s0 = obs.init()
    expect(obs.onFocus(s0, Number.NaN)).toBe(s0)
    expect(obs.onKeydown(s0, Number.NaN)).toBe(s0)
    expect(obs.onBlur(s0, Number.NaN)).toBe(s0)
    expect(obs.onCompositionStart(s0, Number.POSITIVE_INFINITY)).toBe(s0)
    expect(obs.onCompositionEnd(s0, Number.POSITIVE_INFINITY)).toBe(s0)
    expect(obs.tick(s0, Number.NaN)).toBe(s0)
  })

  it('TC-B1-08 onChange exception is swallowed; FSM still transitions', () => {
    const onChange = vi.fn(() => {
      throw new Error('boom')
    })
    const obs = createUserInputObserver({}, onChange)
    let s = obs.init()
    s = obs.onFocus(s, 0)
    expect(() => {
      s = obs.onKeydown(s, 100)
    }).not.toThrow()
    expect(s.active).toBe(true)
    expect(onChange).toHaveBeenCalled()
  })

  it('TC-B1-09 stray compositionend without start: focused → treated as input', () => {
    const obs = createUserInputObserver()
    let s = obs.init()
    s = obs.onFocus(s, 0)
    s = obs.onCompositionEnd(s, 100)
    expect(s.active).toBe(true)
    expect(s.last_input_t).toBe(100)
  })
})
