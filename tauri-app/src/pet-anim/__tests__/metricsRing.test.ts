// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * metricsRing.test.ts — TDD §4.8 (TC-MR-01..06)
 */
import { describe, expect, it } from 'vitest'
import { createMetricsRing } from '../metricsRing'

describe('metricsRing', () => {
  it('TC-MR-01 (P0) record 100 → samples.length=100', () => {
    const r = createMetricsRing(100)
    for (let i = 0; i < 100; i++) r.record(i)
    expect(r.snapshot().samples.length).toBe(100)
  })

  it('TC-MR-02 (P0) record 150 → 保留最后 100', () => {
    const r = createMetricsRing(100)
    for (let i = 0; i < 150; i++) r.record(i)
    const samples = r.snapshot().samples
    expect(samples.length).toBe(100)
    expect(samples[0]).toBe(50)
    expect(samples[99]).toBe(149)
  })

  it('TC-MR-03 (P0) 1..100 → p50≈50.5, p95≈95.05', () => {
    const r = createMetricsRing(100)
    for (let i = 1; i <= 100; i++) r.record(i)
    const s = r.snapshot()
    expect(s.p50).toBeGreaterThan(49)
    expect(s.p50).toBeLessThan(52)
    expect(s.p95).toBeGreaterThan(94)
    expect(s.p95).toBeLessThan(97)
    expect(s.max).toBe(100)
  })

  it('TC-MR-04 (P0) 空 ring → 全 0', () => {
    const s = createMetricsRing().snapshot()
    expect(s.p50).toBe(0)
    expect(s.p95).toBe(0)
    expect(s.max).toBe(0)
    expect(s.samples.length).toBe(0)
  })

  it('TC-MR-05 (P0) snapshot.samples 修改不影响内部状态', () => {
    const r = createMetricsRing(10)
    for (let i = 0; i < 5; i++) r.record(i * 10)
    const s1 = r.snapshot()
    // Cast away readonly to attempt mutation; the contract is that
    // the snapshot is a copy.
    ;(s1.samples as number[]).push(99999)
    const s2 = r.snapshot()
    expect(s2.samples.length).toBe(5)
    expect(Math.max(...s2.samples)).toBe(40)
  })

  it('TC-MR-06 (P1) reset() 后 snapshot 全 0', () => {
    const r = createMetricsRing(100)
    for (let i = 0; i < 50; i++) r.record(i)
    r.reset()
    const s = r.snapshot()
    expect(s.p50).toBe(0)
    expect(s.p95).toBe(0)
    expect(s.max).toBe(0)
    expect(s.samples.length).toBe(0)
  })
})
