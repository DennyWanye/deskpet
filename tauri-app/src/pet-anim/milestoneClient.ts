// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * milestoneClient (pet-anim/D2) — Pet Animation UX v2.
 *
 * Consumes backend-pushed `pet_milestone` ws messages and serialises them
 * into a FIFO queue so two milestones achieved on the same tick don't fire
 * concurrent celebrations (PRD §3 D2 + TDD §4.15-b TC-D2-06).
 *
 * The 5 milestone rules (streak_7d / streak_30d / msgs_1000 /
 * first_custom_prompt / first_pet_naming) live in `backend/memory/milestone.py`
 * — this client only renders the trigger.
 *
 * Pure FSM; caller drives `tick(now_t)` from the 100ms interval.
 */

export type MilestoneKind =
  | 'streak_7d'
  | 'streak_30d'
  | 'msgs_1000'
  | 'first_custom_prompt'
  | 'first_pet_naming'

export interface MilestoneEvent {
  kind: MilestoneKind
  message: string
  achieved_at: number
}

export interface MilestoneOpts {
  /** Per-celebration duration in ms. PRD §3 D2: 3000. */
  celebration_ms?: number
  /** Bubble auto-dismiss in ms (independent of celebration duration). */
  bubble_auto_dismiss_ms?: number
}

export interface MilestoneClientState {
  /** Queue of pending milestones in FIFO order. */
  queue: MilestoneEvent[]
  /** Currently active milestone (null if idle). */
  active: MilestoneEvent | null
  /** When current celebration started. -Infinity if none. */
  active_since: number
}

export interface MilestoneClientCallbacks {
  /** Fires when a new celebration begins. */
  onCelebrationStart?: (ev: MilestoneEvent, now_t: number) => void
  /** Fires when a celebration ends (auto-dismiss or queue drain). */
  onCelebrationEnd?: (ev: MilestoneEvent, now_t: number) => void
}

export interface MilestoneClient {
  init(): MilestoneClientState
  enqueue(s: MilestoneClientState, ev: MilestoneEvent): MilestoneClientState
  tick(s: MilestoneClientState, now_t: number): MilestoneClientState
}

const DEFAULTS: Required<MilestoneOpts> = {
  celebration_ms: 3000,
  bubble_auto_dismiss_ms: 5000,
}

function safeNum(v: number | undefined, fallback: number, mustBePositive = false): number {
  if (v === undefined || !Number.isFinite(v)) return fallback
  if (mustBePositive && v <= 0) return fallback
  return v
}

export function createMilestoneClient(
  rawOpts: MilestoneOpts = {},
  callbacks: MilestoneClientCallbacks = {},
): MilestoneClient {
  const opts: Required<MilestoneOpts> = {
    celebration_ms: safeNum(rawOpts.celebration_ms, DEFAULTS.celebration_ms, true),
    bubble_auto_dismiss_ms: safeNum(
      rawOpts.bubble_auto_dismiss_ms,
      DEFAULTS.bubble_auto_dismiss_ms,
      true,
    ),
  }

  function init(): MilestoneClientState {
    return { queue: [], active: null, active_since: -Infinity }
  }

  function enqueue(s: MilestoneClientState, ev: MilestoneEvent): MilestoneClientState {
    if (!ev || typeof ev.kind !== 'string' || typeof ev.message !== 'string') return s
    return { ...s, queue: [...s.queue, ev] }
  }

  function tick(s: MilestoneClientState, now_t: number): MilestoneClientState {
    if (!Number.isFinite(now_t)) return s

    // Current active expired?
    if (s.active && now_t - s.active_since >= opts.celebration_ms) {
      try {
        callbacks.onCelebrationEnd?.(s.active, now_t)
      } catch {
        /* swallow */
      }
      // Promote next from queue if any.
      if (s.queue.length > 0) {
        const next = s.queue[0]
        try {
          callbacks.onCelebrationStart?.(next, now_t)
        } catch {
          /* swallow */
        }
        return { ...s, queue: s.queue.slice(1), active: next, active_since: now_t }
      }
      return { ...s, active: null, active_since: -Infinity }
    }

    // No active — start next if queue has any.
    if (!s.active && s.queue.length > 0) {
      const next = s.queue[0]
      try {
        callbacks.onCelebrationStart?.(next, now_t)
      } catch {
        /* swallow */
      }
      return { ...s, queue: s.queue.slice(1), active: next, active_since: now_t }
    }

    return s
  }

  return { init, enqueue, tick }
}
