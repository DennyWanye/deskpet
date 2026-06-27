// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * perlinNoise.ts — TDD §2.1 / §3.1.
 *
 * 1D Improved Perlin (Perlin 2002). Pure: no DOM, no time, no rng
 * leakage after construction. The returned function is deterministic
 * given (seed, frequency, amplitude). Caller passes a DOMHighResTimeStamp
 * in ms; we internally compute `t = t_ms * frequency / 1000` so the
 * `frequency` opt is interpreted in Hz (cycles per second) — matching
 * PRD FR-1 "t * 0.0003" when frequency=0.3.
 *
 * Implementation notes:
 *   - permutation table is 256 entries shuffled by a mulberry32 seeded
 *     PRNG, then doubled (size 512) so we never have to `% 256` inside
 *     the hot loop.
 *   - smoothstep is Ken Perlin's quintic 6t^5-15t^4+10t^3.
 *   - gradient set is the 8 minimal 1D-projections that keep the output
 *     evenly distributed in [-1, +1]. Final value is scaled by `amplitude`.
 */
export interface PerlinOpts {
  /** Deterministic seed for the permutation table. Default 1337. */
  seed?: number
  /** Output range [-amplitude, +amplitude]. Default 1. */
  amplitude?: number
  /**
   * Cycles per second (Hz). The function takes t in ms; the internal
   * phase is `t * frequency / 1000`. Default 0.3 Hz (≈ PRD FR-1
   * "t * 0.0003" multiplier).
   */
  frequency?: number
}

export type PerlinFn = (t_ms: number) => number

function mulberry32(seed: number): () => number {
  let s = seed | 0
  return () => {
    s = (s + 0x6d2b79f5) | 0
    let t = s
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function fade(t: number): number {
  // 6t^5 - 15t^4 + 10t^3
  return t * t * t * (t * (t * 6 - 15) + 10)
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t
}

/**
 * 1D gradient: pick a sign × magnitude from a tiny lookup. The 8 values
 * here are a balanced 1D projection (sum = 0; variance ≈ 1) so the
 * resulting noise is centred and roughly unit-amplitude before scaling.
 */
const GRAD1D = [1, -1, 0.5, -0.5, 0.75, -0.75, 0.25, -0.25]

function grad(hash: number, x: number): number {
  return GRAD1D[hash & 7] * x
}

export function createPerlin1D(opts: PerlinOpts = {}): PerlinFn {
  const seed = opts.seed ?? 1337
  const amplitude = opts.amplitude ?? 1
  const frequency = opts.frequency ?? 0.3

  // Build a seeded permutation table of 256, then double it.
  const rng = mulberry32(seed)
  const p = new Uint8Array(256)
  for (let i = 0; i < 256; i++) p[i] = i
  // Fisher-Yates shuffle.
  for (let i = 255; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1))
    const tmp = p[i]
    p[i] = p[j]
    p[j] = tmp
  }
  const perm = new Uint8Array(512)
  for (let i = 0; i < 512; i++) perm[i] = p[i & 255]

  return function perlin1d(t_ms: number): number {
    const phase = t_ms * frequency * 0.001
    const xi = Math.floor(phase) & 255
    const xf = phase - Math.floor(phase)
    const u = fade(xf)
    const g0 = grad(perm[xi], xf)
    const g1 = grad(perm[xi + 1], xf - 1)
    // Raw noise is approximately in (-1, +1). Scale by amplitude.
    const v = lerp(g0, g1, u)
    // Clamp defensively for callers that assume the contract holds.
    if (v > 1) return amplitude
    if (v < -1) return -amplitude
    return v * amplitude
  }
}
