/**
 * Unit tests for dndDetector — F1 (TDD §4.12).
 */
import { describe, it, expect, vi } from 'vitest'
import { createDNDDetector } from '../dndDetector'

describe('dndDetector — F1', () => {
  it('TC-F1-01 fullscreen=true → DND active with reasons=[fullscreen]', async () => {
    const onChange = vi.fn()
    const d = createDNDDetector(
      {},
      {
        fetchFullscreen: async () => true,
        fetchCallActive: async () => false,
        onChange,
      },
    )
    let s = d.init()
    s = await d.tick(s, 0)
    expect(s.active).toBe(true)
    expect(s.reasons).toEqual(['fullscreen'])
    expect(onChange).toHaveBeenCalledWith(true, ['fullscreen'], 0)
  })

  it('TC-F1-02 fullscreen back to false → DND inactive', async () => {
    let fs = true
    const d = createDNDDetector(
      {},
      {
        fetchFullscreen: async () => fs,
        fetchCallActive: async () => false,
      },
    )
    let s = d.init()
    s = await d.tick(s, 0)
    expect(s.active).toBe(true)
    fs = false
    s = await d.tick(s, 1000)
    expect(s.active).toBe(false)
  })

  it('TC-F1-03 (M-19) KPM 200 below threshold → no DND', async () => {
    const d = createDNDDetector(
      { typing_kpm_threshold: 250, typing_window_ms: 60_000 },
      { fetchFullscreen: async () => false, fetchCallActive: async () => false },
    )
    let s = d.init()
    // 200 keys over 60s window → 200 KPM.
    for (let i = 0; i < 200; i++) s = d.notifyKeyEvent(s, i * 300)
    s = await d.tick(s, 60_000)
    expect(s.active).toBe(false)
  })

  it('TC-F1-04 (M-19) KPM 260 above threshold → DND typing', async () => {
    const d = createDNDDetector(
      { typing_kpm_threshold: 250, typing_window_ms: 60_000 },
      { fetchFullscreen: async () => false, fetchCallActive: async () => false },
    )
    let s = d.init()
    // 260 keys in 60s.
    for (let i = 0; i < 260; i++) s = d.notifyKeyEvent(s, i * 230)
    s = await d.tick(s, 60_000)
    expect(s.active).toBe(true)
    expect(s.reasons).toEqual(['typing'])
  })

  it('TC-F1-05 (M-20) call active → DND with reasons including "call"', async () => {
    const d = createDNDDetector(
      {},
      { fetchFullscreen: async () => false, fetchCallActive: async () => true },
    )
    let s = d.init()
    s = await d.tick(s, 0)
    expect(s.reasons).toContain('call')
  })

  it('TC-F1-06 audio API throws → call detection auto-disables, others keep working', async () => {
    const d = createDNDDetector(
      {},
      {
        fetchFullscreen: async () => true, // fullscreen still works
        fetchCallActive: async () => {
          throw new Error('audio session fail')
        },
      },
    )
    let s = d.init()
    s = await d.tick(s, 0)
    expect(s.active).toBe(true)
    expect(s.reasons).toEqual(['fullscreen'])
    expect(s.disabled.has('call')).toBe(true)
  })

  it('TC-F1-07 user disables typing via enabled_triggers', async () => {
    const d = createDNDDetector(
      { enabled_triggers: ['fullscreen', 'call'] },
      { fetchFullscreen: async () => false, fetchCallActive: async () => false },
    )
    let s = d.init()
    for (let i = 0; i < 1000; i++) s = d.notifyKeyEvent(s, i * 50)
    s = await d.tick(s, 60_000)
    expect(s.reasons).not.toContain('typing')
  })

  it('TC-F1-08 (M-20) 6 different process names → same generic audio API path', async () => {
    // The detector doesn't see process names — it just gets a boolean from
    // is_any_audio_capture_active. Verify the boolean propagates uniformly
    // regardless of which app caused it (NO hard-coded process names in
    // this layer).
    const apps = ['Teams', 'Zoom', 'Discord', 'Slack', 'Wechat', 'Lark']
    for (const app of apps) {
      const d = createDNDDetector(
        {},
        {
          fetchFullscreen: async () => false,
          fetchCallActive: async () => {
            void app // marker for which app the rust layer detected
            return true
          },
        },
      )
      let s = d.init()
      s = await d.tick(s, 0)
      expect(s.reasons).toContain('call')
    }
  })

  it('TC-F1-09 invalid now_t → no-op', async () => {
    const d = createDNDDetector(
      {},
      { fetchFullscreen: async () => true, fetchCallActive: async () => false },
    )
    let s = d.init()
    s = await d.tick(s, Number.NaN)
    expect(s.active).toBe(false)
  })
})
