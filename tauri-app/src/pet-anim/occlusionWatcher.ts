// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * occlusionWatcher (pet-anim/E2) — Pet Animation UX v2.
 *
 * Detects when another window covers the pet by ≥ `threshold_ratio` for
 * ≥ `grace_ms` and triggers a side-step via `findSafeSpot`.
 *
 * Algorithm (PRD §3 E2 + M-17):
 *   1. Caller polls `tick(now_t, petRect, screen)` from a 1Hz interval.
 *   2. On each tick, `fetchTopWindows()` returns the foreground windows
 *      list (mocked by tests; in production it's a Tauri Rust command).
 *   3. Compute overlap ratio between pet bbox and every other window's bbox.
 *      Take the max.
 *   4. If max ratio ≥ threshold for ≥ grace_ms continuously → call
 *      `findSafeSpot` to find a new pet position, fire `onOccluded(spot)`.
 *   5. If max ratio < threshold → fire `onClear()` once.
 *
 * findSafeSpotGrid (M-17): samples an 8×6 = 48-cell grid across the screen.
 * For each candidate, computes overlap with all other windows; returns the
 * first candidate with < 10% overlap. Returns null if all 48 fail (caller
 * logs a single console.warn and does not move).
 *
 * Performance budget (M-18): each tick's enumerate_top_windows + overlap
 * math should stay ≤ 30ms. graceful_degrade_threshold_ms triggers a slowdown
 * to 0.2Hz (caller controls; this module reports timing via callbacks).
 *
 * NFR-8: only consumes bbox/title/visibility — never reads window contents.
 */

import type { ScreenBounds, Rect } from './edgeWatcher'

export interface WindowRect {
  x: number
  y: number
  w: number
  h: number
}

export interface TopWindowInfo {
  hwnd: number
  title: string
  rect: WindowRect
  is_visible: boolean
}

export interface OcclusionOpts {
  threshold_ratio?: number
  grace_ms?: number
}

export interface OcclusionState {
  occluded: boolean
  /** When current occlusion window started. -Infinity if not occluded. */
  occluded_since: number
}

export interface OcclusionCallbacks {
  fetchTopWindows: () => Promise<TopWindowInfo[]>
  findSafeSpot?: (
    petRect: Rect,
    screen: ScreenBounds,
    others: TopWindowInfo[],
  ) => { x: number; y: number } | null
  onOccluded?: (spot: { x: number; y: number } | null, now_t: number) => void
  onClear?: (now_t: number) => void
  onPerfTiming?: (ms: number) => void
}

export interface OcclusionWatcher {
  init(): OcclusionState
  tick(
    s: OcclusionState,
    now_t: number,
    petRect: Rect,
    screen: ScreenBounds,
  ): Promise<OcclusionState>
}

const DEFAULTS: Required<OcclusionOpts> = {
  threshold_ratio: 0.5,
  grace_ms: 5000,
}

function safeNum(v: number | undefined, fallback: number, mustBePositive = false): number {
  if (v === undefined || !Number.isFinite(v)) return fallback
  if (mustBePositive && v <= 0) return fallback
  return v
}

/** Overlap area between two axis-aligned rects. */
function overlapArea(a: Rect | WindowRect, b: Rect | WindowRect): number {
  const x_overlap = Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x))
  const y_overlap = Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y))
  return x_overlap * y_overlap
}

export function overlapRatio(petRect: Rect, other: WindowRect): number {
  const a = petRect.w * petRect.h
  if (a <= 0) return 0
  return overlapArea(petRect, other) / a
}

/**
 * 8x6 grid sampling — picks first cell with < 10% overlap with all other windows.
 * Returns the cell's top-left corner. M-17 round-1 evidence.
 */
export function findSafeSpotGrid(
  petRect: Rect,
  screen: ScreenBounds,
  others: TopWindowInfo[],
  cols = 8,
  rows = 6,
  max_overlap_ratio = 0.1,
): { x: number; y: number } | null {
  if (!screen || screen.width <= 0 || screen.height <= 0) return null
  // Cell size such that pet fits inside.
  const usable_w = Math.max(0, screen.width - petRect.w)
  const usable_h = Math.max(0, screen.height - petRect.h)
  if (usable_w === 0 && usable_h === 0) return null
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const x = Math.round((c / Math.max(1, cols - 1)) * usable_w)
      const y = Math.round((r / Math.max(1, rows - 1)) * usable_h)
      // Clamp to non-negative (AC-10-02: never push pet off-screen).
      const cand: Rect = {
        x: Math.max(0, x),
        y: Math.max(0, y),
        w: petRect.w,
        h: petRect.h,
      }
      let worst_overlap = 0
      for (const o of others) {
        if (!o.is_visible) continue
        const ratio = overlapArea(cand, o.rect) / (petRect.w * petRect.h)
        if (ratio > worst_overlap) worst_overlap = ratio
      }
      if (worst_overlap < max_overlap_ratio) {
        return { x: cand.x, y: cand.y }
      }
    }
  }
  return null
}

export function createOcclusionWatcher(
  rawOpts: OcclusionOpts,
  callbacks: OcclusionCallbacks,
): OcclusionWatcher {
  const opts: Required<OcclusionOpts> = {
    threshold_ratio: safeNum(rawOpts.threshold_ratio, DEFAULTS.threshold_ratio, true),
    grace_ms: safeNum(rawOpts.grace_ms, DEFAULTS.grace_ms, true),
  }

  function init(): OcclusionState {
    return { occluded: false, occluded_since: -Infinity }
  }

  async function tick(
    s: OcclusionState,
    now_t: number,
    petRect: Rect,
    screen: ScreenBounds,
  ): Promise<OcclusionState> {
    if (!Number.isFinite(now_t)) return s
    const t0 = now_t
    let others: TopWindowInfo[] = []
    try {
      others = await callbacks.fetchTopWindows()
    } catch {
      // Win32 API failure → silent disable (per PRD §3 E2 graceful degrade).
      return s
    }
    const t1 = typeof performance !== 'undefined' ? performance.now() : now_t
    callbacks.onPerfTiming?.(t1 - t0)

    // Compute max overlap with visible others (excluding the pet itself by
    // assumption: caller filters its own hwnd before pushing into the list).
    let max_ratio = 0
    for (const o of others) {
      if (!o.is_visible) continue
      const r = overlapRatio(petRect, o.rect)
      if (r > max_ratio) max_ratio = r
    }

    if (max_ratio >= opts.threshold_ratio) {
      if (!s.occluded && s.occluded_since === -Infinity) {
        // Start grace window.
        return { occluded: false, occluded_since: now_t }
      }
      if (!s.occluded && now_t - s.occluded_since >= opts.grace_ms) {
        // Crossed grace → trigger.
        const spot = callbacks.findSafeSpot
          ? callbacks.findSafeSpot(petRect, screen, others)
          : findSafeSpotGrid(petRect, screen, others)
        try {
          callbacks.onOccluded?.(spot, now_t)
        } catch {
          /* swallow */
        }
        return { occluded: true, occluded_since: s.occluded_since }
      }
      return s
    }

    // Below threshold.
    if (s.occluded) {
      try {
        callbacks.onClear?.(now_t)
      } catch {
        /* swallow */
      }
      return { occluded: false, occluded_since: -Infinity }
    }
    if (s.occluded_since !== -Infinity) {
      // Reset grace window (occlusion didn't last long enough).
      return { occluded: false, occluded_since: -Infinity }
    }
    return s
  }

  return { init, tick }
}
