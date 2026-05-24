/**
 * metricsRing.ts — TDD §2.8.
 *
 * Fixed-capacity ring buffer of latency samples with p50 / p95 / max
 * snapshots. Used by FR-7 to expose interaction_latency and visual_latency
 * via `window.__deskpet_anim_metrics()`.
 *
 * Snapshot returns a frozen / readonly slice so external code can't mutate
 * the ring (caught by Round-1 review mr-Round1-m6).
 */
export interface MetricsSnapshot {
  p50: number
  p95: number
  max: number
  samples: ReadonlyArray<number>
}

export interface MetricsRing {
  record(latency_ms: number): void
  snapshot(): MetricsSnapshot
  reset(): void
}

function percentile(sorted: number[], q: number): number {
  if (sorted.length === 0) return 0
  // Linear interpolation between closest ranks.
  const idx = (sorted.length - 1) * q
  const lo = Math.floor(idx)
  const hi = Math.ceil(idx)
  if (lo === hi) return sorted[lo]
  const frac = idx - lo
  return sorted[lo] * (1 - frac) + sorted[hi] * frac
}

export function createMetricsRing(capacity = 100): MetricsRing {
  const cap = Math.max(1, Math.floor(capacity))
  let buf: number[] = []
  return {
    record(latency_ms: number) {
      if (!Number.isFinite(latency_ms)) return
      buf.push(latency_ms)
      if (buf.length > cap) buf.shift()
    },
    snapshot(): MetricsSnapshot {
      if (buf.length === 0) {
        return { p50: 0, p95: 0, max: 0, samples: [] }
      }
      const sorted = [...buf].sort((a, b) => a - b)
      return {
        p50: percentile(sorted, 0.5),
        p95: percentile(sorted, 0.95),
        max: sorted[sorted.length - 1],
        samples: [...buf],
      }
    },
    reset() {
      buf = []
    },
  }
}
