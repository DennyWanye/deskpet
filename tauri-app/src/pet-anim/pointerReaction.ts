// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * pointerReaction.ts — TDD §2.7 (v3 — rename + 3-tuple transition table).
 *
 * State machine that turns raw pointer events into "reactions": single
 * click pulse, double-click pulse, hover enter/leave. The classic
 * single-vs-double-click puzzle is handled by deferring single emission
 * until either a second click arrives within `double_click_threshold_ms`
 * (→ promote to double) or the threshold elapses (→ emit single).
 *
 * Key v3 corrections (Round-2 review M'1):
 *   - The state enum lost 'hovering'; hover is tracked independently
 *     via `is_hovering: boolean` so a hover ⨯ pulse combination doesn't
 *     require an N×M state explosion.
 *   - Pulse states (in_click_pulse / in_double_pulse) ignore additional
 *     onClick — third click within a double pulse is swallowed by design.
 *   - is_hovering already-true → onPointerEnter emits nothing (de-bounces
 *     debounce-window reentries).
 */
export type InteractionKind =
  | 'hover_enter'
  | 'hover_leave'
  | 'click'
  | 'double_click'

export type ReactorState =
  | 'rest'
  | 'pending_single'
  | 'in_click_pulse'
  | 'in_double_pulse'

export interface ReactorContext {
  state: ReactorState
  is_hovering: boolean
  /** Timestamp the pending single is being held. */
  pending_click_ts: number
  /** Timestamp the active pulse started. */
  pulse_start_ts: number
}

export interface PointerReactionOpts {
  /** Window in which a 2nd click is considered a double. Default 300 ms. */
  double_click_threshold_ms?: number
  /** Hover debounce (currently informational; we de-dup enter/leave). */
  hover_debounce_ms?: number
  /** Click pulse duration. Default 200 ms. */
  click_pulse_ms?: number
  /** Double-click pulse duration. Default 400 ms. */
  double_pulse_ms?: number
}

export interface ReactorStep {
  ctx: ReactorContext
  effect: InteractionKind | null
}

export interface PointerReactor {
  init(): ReactorContext
  /** now_t is accepted for symmetry with the rest of the API even though
   * enter/leave currently don't need it; future debouncing may. */
  onPointerEnter(ctx: ReactorContext, now_t?: number): ReactorStep
  onPointerLeave(ctx: ReactorContext, now_t?: number): ReactorStep
  onClick(ctx: ReactorContext, now_t: number): ReactorStep
  tick(ctx: ReactorContext, now_t: number): ReactorStep
}

export function createPointerReactor(
  opts: PointerReactionOpts = {},
): PointerReactor {
  const dbl_threshold = opts.double_click_threshold_ms ?? 300
  const click_pulse_ms = opts.click_pulse_ms ?? 200
  const double_pulse_ms = opts.double_pulse_ms ?? 400

  return {
    init(): ReactorContext {
      return {
        state: 'rest',
        is_hovering: false,
        pending_click_ts: 0,
        pulse_start_ts: 0,
      }
    },

    onPointerEnter(ctx) {
      // Already hovering → de-dup, no emit.
      if (ctx.is_hovering) return { ctx, effect: null }
      return {
        ctx: { ...ctx, is_hovering: true },
        effect: 'hover_enter',
      }
    },

    onPointerLeave(ctx) {
      if (!ctx.is_hovering) return { ctx, effect: null }
      return {
        ctx: { ...ctx, is_hovering: false },
        effect: 'hover_leave',
      }
    },

    onClick(ctx, now_t) {
      switch (ctx.state) {
        case 'rest':
          return {
            ctx: { ...ctx, state: 'pending_single', pending_click_ts: now_t },
            effect: null,
          }
        case 'pending_single': {
          // Within double_click threshold? Promote to double.
          const dt = now_t - ctx.pending_click_ts
          if (dt <= dbl_threshold) {
            return {
              ctx: {
                ...ctx,
                state: 'in_double_pulse',
                pulse_start_ts: now_t,
                pending_click_ts: 0,
              },
              effect: 'double_click',
            }
          }
          // Past threshold — but we got here without a tick advancing
          // pending_single → in_click_pulse. Promote as if user clicked
          // twice: emit the prior pending as click, then start a new
          // pending_single for this click. The TICK fast-path normally
          // handles this; we mirror the behaviour for safety.
          return {
            ctx: { ...ctx, state: 'pending_single', pending_click_ts: now_t },
            effect: 'click',
          }
        }
        case 'in_click_pulse':
          // Mid-pulse: a new click promotes to double pulse.
          return {
            ctx: {
              ...ctx,
              state: 'in_double_pulse',
              pulse_start_ts: now_t,
              pending_click_ts: 0,
            },
            effect: 'double_click',
          }
        case 'in_double_pulse':
          // Swallow — designed silent window (PRD FR-6 v3 clarification).
          return { ctx, effect: null }
      }
    },

    tick(ctx, now_t) {
      switch (ctx.state) {
        case 'rest':
          return { ctx, effect: null }
        case 'pending_single': {
          if (now_t - ctx.pending_click_ts >= dbl_threshold) {
            return {
              ctx: {
                ...ctx,
                state: 'in_click_pulse',
                pulse_start_ts: now_t,
                pending_click_ts: 0,
              },
              effect: 'click',
            }
          }
          return { ctx, effect: null }
        }
        case 'in_click_pulse': {
          if (now_t - ctx.pulse_start_ts >= click_pulse_ms) {
            return {
              ctx: { ...ctx, state: 'rest', pulse_start_ts: 0 },
              effect: null,
            }
          }
          return { ctx, effect: null }
        }
        case 'in_double_pulse': {
          if (now_t - ctx.pulse_start_ts >= double_pulse_ms) {
            return {
              ctx: { ...ctx, state: 'rest', pulse_start_ts: 0 },
              effect: null,
            }
          }
          return { ctx, effect: null }
        }
      }
    },
  }
}
