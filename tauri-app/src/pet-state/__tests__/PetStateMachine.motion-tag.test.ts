/**
 * PetStateMachine — motion_tag_pool extension tests (PRD FR-5).
 * TC-PSM-01 / TC-PSM-02 from TDD §4.13.
 */
import { describe, expect, it } from 'vitest'
import { STATE_CONFIG } from '../PetStateMachine'

describe('PetStateMachine.motion_tag_pool', () => {
  it('TC-PSM-01 (P0) STATE_CONFIG.worried.motion_tag_pool === ["slow","medium"]', () => {
    expect(STATE_CONFIG.worried.motion_tag_pool).toEqual(['slow', 'medium'])
    expect(STATE_CONFIG.working.motion_tag_pool).toEqual(['fast', 'medium'])
    expect(STATE_CONFIG.alert.motion_tag_pool).toEqual(['slow', 'special'])
    expect(STATE_CONFIG.intervening.motion_tag_pool).toEqual(['fast'])
    expect(STATE_CONFIG.idle.motion_tag_pool).toBeUndefined()
  })

  it('TC-PSM-02 (P0) PetMotionConfig 类型字段存在', () => {
    const cfg = STATE_CONFIG.worried
    // Existence assertion — `motion_tag_pool` must be on the shape even
    // if undefined for some states.
    expect('motion_tag_pool' in cfg).toBe(true)
  })
})
