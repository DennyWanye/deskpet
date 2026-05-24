/**
 * Shared test utilities for the pet-anim suite (TDD §5.1).
 *
 * All time-aware modules accept an injected clock and rng so tests can
 * be deterministic. These helpers wrap the mulberry32 PRNG and an
 * advance-only fake clock that mirrors DOMHighResTimeStamp semantics
 * (monotonically increasing milliseconds).
 */
export function fakeRng(seed: number): () => number {
  let s = seed
  return () => {
    s = (s + 0x6d2b79f5) | 0
    let t = s
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

export function fakeClock(start = 0): {
  now: () => number
  advance: (ms: number) => void
  set: (ms: number) => void
} {
  let t = start
  return {
    now: () => t,
    advance: (ms: number) => {
      t += ms
    },
    set: (ms: number) => {
      t = ms
    },
  }
}

/**
 * Box-Muller normal sample using a callable rng. Used in blinkScheduler
 * tests to predict expected interval distributions.
 */
export function boxMuller(rng: () => number): number {
  const u1 = Math.max(rng(), 1e-12)
  const u2 = rng()
  return Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2)
}
