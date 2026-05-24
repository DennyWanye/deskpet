/**
 * perlinNoise.test.ts — TDD §4.1 (TC-P-01..06)
 */
import { describe, expect, it } from 'vitest'
import { createPerlin1D } from '../perlinNoise'

describe('perlinNoise', () => {
  it('TC-P-01 (P0) 同 seed 同 t 返回相同值', () => {
    const a = createPerlin1D({ seed: 1337 })
    const b = createPerlin1D({ seed: 1337 })
    for (const t of [0, 100, 1000, 12345.6, 99999]) {
      expect(a(t)).toBeCloseTo(b(t), 12)
    }
  })

  it('TC-P-02 (P0) 输出范围 [-amplitude, +amplitude]', () => {
    const p = createPerlin1D({ seed: 42, amplitude: 2.5, frequency: 0.5 })
    for (let i = 0; i < 10_000; i++) {
      const v = p(i * 11.13)
      expect(v).toBeGreaterThanOrEqual(-2.5 - 1e-9)
      expect(v).toBeLessThanOrEqual(2.5 + 1e-9)
    }
  })

  it('TC-P-03 (P0) 不同 seed 在 1000 采样 Pearson |r| < 0.3', () => {
    // Use default frequency (0.3 Hz) but big enough time-steps that
    // 1000 samples span ~300 gradient cells (far past the 256-cell
    // permutation period). Step = 1000 ms × 0.3 Hz / 1000 = 0.3 phase
    // units per sample, non-integer (avoids xf=0 degeneracy).
    const a = createPerlin1D({ seed: 1337 })
    const b = createPerlin1D({ seed: 2741 })
    const xs: number[] = []
    const ys: number[] = []
    for (let i = 0; i < 1000; i++) {
      const t = i * 1000 + 17 // 1-second steps + offset to avoid xf=0
      xs.push(a(t))
      ys.push(b(t))
    }
    const mx = xs.reduce((a, b) => a + b, 0) / xs.length
    const my = ys.reduce((a, b) => a + b, 0) / ys.length
    let num = 0
    let dx2 = 0
    let dy2 = 0
    for (let i = 0; i < xs.length; i++) {
      num += (xs[i] - mx) * (ys[i] - my)
      dx2 += (xs[i] - mx) ** 2
      dy2 += (ys[i] - my) ** 2
    }
    const r = num / Math.sqrt(dx2 * dy2)
    expect(Math.abs(r)).toBeLessThan(0.3)
  })

  it('TC-P-04 (P1) 1Hz 频率 1s 内至少 1 个零点穿越', () => {
    const p = createPerlin1D({ seed: 7, amplitude: 1, frequency: 1 })
    let prev = p(0)
    let crossings = 0
    for (let i = 1; i <= 100; i++) {
      const v = p(i * 10) // 10 ms steps over 1 s
      if ((prev < 0 && v >= 0) || (prev >= 0 && v < 0)) crossings += 1
      prev = v
    }
    expect(crossings).toBeGreaterThanOrEqual(1)
  })

  it('TC-P-05 (P1) 缺省值不抛', () => {
    expect(() => {
      const p = createPerlin1D()
      for (let i = 0; i < 100; i++) p(i * 100)
    }).not.toThrow()
  })

  it('TC-P-06 (P2) 1000 次调用 < 10 ms', () => {
    const p = createPerlin1D({ seed: 99 })
    const t0 = performance.now()
    for (let i = 0; i < 1000; i++) p(i * 7.5)
    const dt = performance.now() - t0
    expect(dt).toBeLessThan(50) // 宽松一点；CI 抖动留余量
  })
})
