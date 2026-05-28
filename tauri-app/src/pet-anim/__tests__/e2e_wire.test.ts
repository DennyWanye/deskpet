// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * e2e_wire.test.ts — TDD §4.12 (TC-E2E-01..03).
 *
 * End-to-end wire test that combines real PetStateMachine + real
 * AnimationOverlay + stub coreModel + stub motion player. Verifies the
 * App.tsx contract: state transitions immediately push tagged motion
 * choices through to the player when state_changed=true.
 */
import { describe, expect, it } from 'vitest'
import { PetStateMachine, STATE_CONFIG } from '../../pet-state/PetStateMachine'
import type { SessionState } from '../../stores/sessionsStore'
import { AnimationOverlay } from '../index'
import type { MotionTag } from '../motionPicker'
import { HIYORI_PARAMS, makeStubCoreModel } from './_stubModel'

function makeSession(partial: Partial<SessionState> = {}): SessionState {
  return {
    sid: 'sess',
    status: 'idle',
    age_seconds: 0,
    last_event_at_ms: Date.now(),
    last_user_at_ms: Date.now(),
    repeat_count: 0,
    supervisor_severity: undefined,
    supervisor_alert: null,
    ...partial,
  } as SessionState
}

describe('e2e wire — PetStateMachine + AnimationOverlay', () => {
  it('TC-E2E-01 (P0) red alert → tick → state_changed=true → setMotionTagPool 立即触发 slow/special', () => {
    const labels: Record<MotionTag, number[]> = {
      fast: [1, 2],
      medium: [5],
      slow: [7, 8],
      special: [9],
    }
    const overlay = new AnimationOverlay({ motionLabelsLoader: () => labels })
    const played: Array<[string, number]> = []
    overlay.setMotionPlayer((g, i) => played.push([g, i]))

    const sm = new PetStateMachine()
    const sid = 'sess'
    const sessions = {
      [sid]: makeSession({
        sid,
        supervisor_severity: 'red',
        supervisor_alert: {
          alert_id: 'a1',
          severity: 'red',
          action: 'nudge',
          diagnosis: '',
          user_message: 'help',
          suggested_buttons: [],
          received_at: Date.now(),
        },
      }),
    } as Record<string, SessionState>

    const result = sm.tick({ sessions })
    expect(result.state).toBe('alert')
    expect(result.state_changed).toBe(true)
    // App.tsx contract: state_changed=true → force_switch_now=true
    overlay.setMotionTagPool(
      STATE_CONFIG.alert.motion_tag_pool ?? [],
      { force_switch_now: true },
      0,
    )
    expect(played.length).toBeGreaterThan(0)
    const playedIdxs = played.map((p) => p[1])
    const allowed = [...labels.slow, ...labels.special]
    for (const idx of playedIdxs) expect(allowed).toContain(idx)
  })

  it('TC-E2E-02 (P0) working → worried 切换, 第一时间 motion idx 属 slow/medium', () => {
    const labels: Record<MotionTag, number[]> = {
      fast: [1, 2],
      medium: [5],
      slow: [7, 8],
      special: [9],
    }
    const overlay = new AnimationOverlay({ motionLabelsLoader: () => labels })
    const played: Array<[string, number]> = []
    overlay.setMotionPlayer((g, i) => played.push([g, i]))

    // Establish working state first.
    overlay.setMotionTagPool(
      STATE_CONFIG.working.motion_tag_pool ?? [],
      { force_switch_now: true },
      0,
    )
    played.length = 0

    // Transition to worried (yellow).
    overlay.setMotionTagPool(
      STATE_CONFIG.worried.motion_tag_pool ?? [],
      { force_switch_now: true },
      1000,
    )
    expect(played.length).toBeGreaterThan(0)
    const idx = played[played.length - 1][1]
    expect([...labels.slow, ...labels.medium]).toContain(idx)
  })

  it('TC-E2E-03 (P1) localStorage 完全空 → 端到端不抛, 按 PetStateMachine 现有行为', () => {
    const overlay = new AnimationOverlay({
      // No labels loader available → empty pool → no motion call but
      // also no crash.
      motionLabelsLoader: () => null,
    })
    const played: Array<[string, number]> = []
    overlay.setMotionPlayer((g, i) => played.push([g, i]))
    overlay.setMotionTagPool(['slow', 'special'], { force_switch_now: true }, 0)
    expect(played.length).toBe(0)

    // Render loop must not throw either.
    const stub = makeStubCoreModel(HIYORI_PARAMS)
    expect(() => overlay.applyTo(stub.coreModel, 0)).not.toThrow()
  })
})
