// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * visemeLipsync (pet-anim/B3 main path) — Pet Animation UX v2.
 *
 * Consumes a stream of timestamped viseme codes (either from the backend
 * `tts_viseme` ws message, or from the frontend phonemeEstimator fallback)
 * and produces per-frame (mouthY, mouthForm) values for AnimationOverlay
 * step 1.
 *
 * Algorithm
 *   - Maintain a sorted FIFO queue of frames { v, t_ms }.
 *   - sample(now_t) finds the most recent frame ≤ now_t (the "current" v).
 *   - If the next frame exists and we're within `blend_ms` of the *next*
 *     frame's start (i.e. the transition window), linearly blend toward it.
 *   - If the most recent frame is older than `silent_after_ms` with no
 *     newer frame queued, return silent (mouthY=0, mouthForm=0).
 *
 * Viseme → (mouthY, mouthForm) mapping
 *   A   open jaw, neutral form       mouthY 0.70  mouthForm  0
 *   I   tight, spread                mouthY 0.20  mouthForm  0.50
 *   U   round, small                 mouthY 0.30  mouthForm -0.50
 *   E   open spread                  mouthY 0.40  mouthForm  0.30
 *   O   round, open                  mouthY 0.60  mouthForm -0.30
 *   silent                           mouthY 0     mouthForm  0
 *
 * NFR-6: every now_t is DOMHighResTimeStamp passed by the caller.
 */

export type VisemeCode = 'A' | 'I' | 'U' | 'E' | 'O' | 'silent'

export interface VisemeFrame {
  v: VisemeCode
  /** Absolute DOMHighResTimeStamp when this viseme should be "in effect". */
  t_ms: number
}

export interface VisemeOpts {
  /** Blend duration in ms between consecutive frames. PRD §3 B3: 60. */
  blend_ms?: number
  /** If no frame is newer than this many ms past now_t, fall to silent. */
  silent_after_ms?: number
}

export interface VisemeLipsync {
  push(frame: VisemeFrame): void
  pushMany(frames: VisemeFrame[]): void
  sample(now_t: number): { mouthY: number; mouthForm: number; v: VisemeCode }
  flush(): void
  /** Diagnostic snapshot. */
  debug(): { queue_size: number; last_v: VisemeCode }
}

const DEFAULTS: Required<VisemeOpts> = {
  blend_ms: 60,
  silent_after_ms: 300,
}

const VISEME_MAP: Record<VisemeCode, { mouthY: number; mouthForm: number }> = {
  A: { mouthY: 0.7, mouthForm: 0 },
  I: { mouthY: 0.2, mouthForm: 0.5 },
  U: { mouthY: 0.3, mouthForm: -0.5 },
  E: { mouthY: 0.4, mouthForm: 0.3 },
  O: { mouthY: 0.6, mouthForm: -0.3 },
  silent: { mouthY: 0, mouthForm: 0 },
}

function safeNum(v: number | undefined, fallback: number, mustBePositive = false): number {
  if (v === undefined || !Number.isFinite(v)) return fallback
  if (mustBePositive && v <= 0) return fallback
  return v
}

function clamp01(v: number): number {
  return Math.max(0, Math.min(1, v))
}

function lerp(a: number, b: number, t01: number): number {
  return a + (b - a) * clamp01(t01)
}

export function createVisemeLipsync(rawOpts: VisemeOpts = {}): VisemeLipsync {
  const opts: Required<VisemeOpts> = {
    blend_ms: safeNum(rawOpts.blend_ms, DEFAULTS.blend_ms, true),
    silent_after_ms: safeNum(rawOpts.silent_after_ms, DEFAULTS.silent_after_ms, true),
  }

  // Sorted by t_ms ascending; new frames appended/inserted in order.
  const queue: VisemeFrame[] = []
  let last_sampled: VisemeCode = 'silent'

  function push(frame: VisemeFrame): void {
    if (!frame || typeof frame.v !== 'string' || !Number.isFinite(frame.t_ms)) return
    if (!(frame.v in VISEME_MAP)) return
    // Insert maintaining sort order (binary search would be overkill for typical sizes).
    if (queue.length === 0 || frame.t_ms >= queue[queue.length - 1].t_ms) {
      queue.push(frame)
      return
    }
    let i = queue.length - 1
    while (i >= 0 && queue[i].t_ms > frame.t_ms) i--
    queue.splice(i + 1, 0, frame)
  }

  function pushMany(frames: VisemeFrame[]): void {
    if (!Array.isArray(frames)) return
    for (const f of frames) push(f)
  }

  function sample(now_t: number): { mouthY: number; mouthForm: number; v: VisemeCode } {
    if (!Number.isFinite(now_t) || queue.length === 0) {
      return { ...VISEME_MAP.silent, v: 'silent' }
    }

    // Find current frame index: largest i with queue[i].t_ms <= now_t.
    let curIdx = -1
    for (let i = queue.length - 1; i >= 0; i--) {
      if (queue[i].t_ms <= now_t) {
        curIdx = i
        break
      }
    }
    if (curIdx === -1) {
      // now_t earlier than first frame → silent until first frame.
      last_sampled = 'silent'
      return { ...VISEME_MAP.silent, v: 'silent' }
    }

    const cur = queue[curIdx]
    const next = queue[curIdx + 1]
    const curVals = VISEME_MAP[cur.v]

    // Silence ceiling: if current frame is much older than now and no next exists,
    // decay to silent (handles "stream stopped" case before tts_end / fade kicks in).
    if (!next && now_t - cur.t_ms > opts.silent_after_ms) {
      last_sampled = 'silent'
      return { ...VISEME_MAP.silent, v: 'silent' }
    }

    // Blend window: if next exists and we're approaching it, lerp.
    if (next) {
      const dist_to_next = next.t_ms - now_t
      if (dist_to_next < opts.blend_ms && dist_to_next > 0) {
        // We are inside the blend lead-in to next: t in [0,1] where 0 is start of blend window.
        const t01 = 1 - dist_to_next / opts.blend_ms
        const nextVals = VISEME_MAP[next.v]
        last_sampled = cur.v
        return {
          mouthY: lerp(curVals.mouthY, nextVals.mouthY, t01),
          mouthForm: lerp(curVals.mouthForm, nextVals.mouthForm, t01),
          v: cur.v,
        }
      }
    }

    last_sampled = cur.v
    return { ...curVals, v: cur.v }
  }

  function flush(): void {
    queue.length = 0
    last_sampled = 'silent'
  }

  function debug(): { queue_size: number; last_v: VisemeCode } {
    return { queue_size: queue.length, last_v: last_sampled }
  }

  return { push, pushMany, sample, flush, debug }
}

export { VISEME_MAP }
