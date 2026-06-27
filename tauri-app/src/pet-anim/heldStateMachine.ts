// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * heldStateMachine (pet-anim/A1) — Pet Animation UX v2.
 *
 * Pure FSM modelling drag → being_held → spring_back per PRD §3 A1 / TDD §2.1.
 *
 * State machine
 *   idle           — no wobble, no surprise.
 *   being_held     — sinusoidal wobble with exponential decay; surprise pulse
 *                    that linearly fades over `surprise_duration_ms`.
 *   spring_back    — ease-out cubic lerp from last wobble → 0 over
 *                    `spring_back_ms`, then auto-returns to idle.
 *
 * Wobble formula (PRD §3 A1):
 *   wobble = amplitude × sin(2π × dt / period) × exp(-dt / decay_const)
 *
 * Orthogonality (PRD §3 A1 v1 兼容 / TC-A1-06):
 *   The caller must enter this FSM only after observing > 5px pointer movement,
 *   so a pure click (mousedown+mouseup with no move) still routes through v1's
 *   pulseInteraction('click') and triggers TapBody as before. The FSM here is
 *   agnostic to that — it just transitions on explicit onDragStart/onDragEnd.
 *
 * NFR-6 (clock injection):
 *   All `now_t` parameters are DOMHighResTimeStamp passed by the caller; no
 *   internal `performance.now()` / `Date.now()` calls.
 *
 * Pure-function style: contexts in, new contexts out. No internal mutable state
 * beyond closed-over opts.
 */

export type DragState = 'idle' | 'being_held' | 'spring_back'

export interface DragOpts {
  /** Peak wobble amplitude in degrees. Default 8. */
  wobble_amplitude_deg?: number
  /** Sinusoidal period in ms. Default 300. */
  wobble_period_ms?: number
  /** Exponential decay time constant in ms (OQ-A1 candidate 4000/6000/8000). Default 4000. */
  wobble_decay_const_ms?: number
  /** Spring-back ease-out duration in ms. Default 250. */
  spring_back_ms?: number
  /** Surprise pulse linear decay duration in ms. Default 200. */
  surprise_duration_ms?: number
}

export interface DragContext {
  state: DragState
  /** When the current being_held started. -Infinity in idle. */
  held_start_t: number
  /** When the current spring_back started. -Infinity outside spring_back. */
  release_t: number
  /** Wobble value (degrees) captured at the moment of release. */
  release_wobble_deg: number
}

export interface DragStep {
  ctx: DragContext
  /** Additive degrees to add to ParamBodyAngleZ (§6.2 matrix row "ParamBodyAngleZ" ADD). */
  wobble_delta: number
  /** 0..1 multiplier for surprise face params (PRD §3 A1: MouthForm=-0.5, EyeLOpen/ROpen=1.3, BrowLY/RY=+0.5). */
  surprise_factor: number
}

export interface DragStateMachine {
  init(): DragContext
  onDragStart(ctx: DragContext, now_t: number): DragStep
  onDragEnd(ctx: DragContext, now_t: number): DragStep
  tick(ctx: DragContext, now_t: number): DragStep
}

const DEFAULTS: Required<DragOpts> = {
  wobble_amplitude_deg: 8,
  wobble_period_ms: 300,
  wobble_decay_const_ms: 4000,
  spring_back_ms: 250,
  surprise_duration_ms: 200,
}

function safeNum(v: number | undefined, fallback: number, mustBePositive = false): number {
  if (v === undefined || !Number.isFinite(v)) return fallback
  if (mustBePositive && v <= 0) return fallback
  return v
}

function makeIdleCtx(): DragContext {
  return {
    state: 'idle',
    held_start_t: -Infinity,
    release_t: -Infinity,
    release_wobble_deg: 0,
  }
}

function computeWobble(opts: Required<DragOpts>, dt_ms: number): number {
  if (!Number.isFinite(dt_ms) || dt_ms < 0) return 0
  const angle = (2 * Math.PI * dt_ms) / opts.wobble_period_ms
  const decay = Math.exp(-dt_ms / opts.wobble_decay_const_ms)
  return opts.wobble_amplitude_deg * Math.sin(angle) * decay
}

/** Ease-out cubic: t in [0,1] → 1 - (1-t)^3. */
function easeOutCubic(t01: number): number {
  const x = Math.max(0, Math.min(1, t01))
  const inv = 1 - x
  return 1 - inv * inv * inv
}

function emptyStep(ctx: DragContext): DragStep {
  return { ctx, wobble_delta: 0, surprise_factor: 0 }
}

export function createDragStateMachine(rawOpts: DragOpts = {}): DragStateMachine {
  const opts: Required<DragOpts> = {
    wobble_amplitude_deg: safeNum(rawOpts.wobble_amplitude_deg, DEFAULTS.wobble_amplitude_deg),
    wobble_period_ms: safeNum(rawOpts.wobble_period_ms, DEFAULTS.wobble_period_ms, true),
    wobble_decay_const_ms: safeNum(
      rawOpts.wobble_decay_const_ms,
      DEFAULTS.wobble_decay_const_ms,
      true,
    ),
    spring_back_ms: safeNum(rawOpts.spring_back_ms, DEFAULTS.spring_back_ms, true),
    surprise_duration_ms: Math.max(
      0,
      safeNum(rawOpts.surprise_duration_ms, DEFAULTS.surprise_duration_ms),
    ),
  }

  function tick(ctx: DragContext, now_t: number): DragStep {
    if (!Number.isFinite(now_t)) return emptyStep(ctx)

    if (ctx.state === 'being_held') {
      const dt = now_t - ctx.held_start_t
      const wobble = computeWobble(opts, dt)
      const surprise =
        opts.surprise_duration_ms === 0
          ? 0
          : Math.max(0, 1 - dt / opts.surprise_duration_ms)
      return { ctx, wobble_delta: wobble, surprise_factor: surprise }
    }

    if (ctx.state === 'spring_back') {
      const dt = now_t - ctx.release_t
      if (dt >= opts.spring_back_ms) {
        return { ctx: makeIdleCtx(), wobble_delta: 0, surprise_factor: 0 }
      }
      // ease-out cubic on completion fraction; remaining magnitude = 1 - eased.
      const eased = easeOutCubic(dt / opts.spring_back_ms)
      const remaining = 1 - eased
      return {
        ctx,
        wobble_delta: ctx.release_wobble_deg * remaining,
        surprise_factor: 0,
      }
    }

    return emptyStep(ctx)
  }

  function onDragStart(ctx: DragContext, now_t: number): DragStep {
    if (!Number.isFinite(now_t)) return emptyStep(ctx)
    // Re-entering being_held (e.g. successive drags) resets the start time.
    const next: DragContext = {
      state: 'being_held',
      held_start_t: now_t,
      release_t: -Infinity,
      release_wobble_deg: 0,
    }
    // First moment: sin(0)=0 wobble, surprise=1 (full pulse).
    return { ctx: next, wobble_delta: 0, surprise_factor: 1 }
  }

  function onDragEnd(ctx: DragContext, now_t: number): DragStep {
    if (!Number.isFinite(now_t)) return emptyStep(ctx)
    if (ctx.state !== 'being_held') {
      // Idempotent: end-without-start (or already releasing) leaves caller at idle.
      return emptyStep(makeIdleCtx())
    }
    const dt = now_t - ctx.held_start_t
    const last_wobble = computeWobble(opts, dt)
    const next: DragContext = {
      state: 'spring_back',
      held_start_t: ctx.held_start_t,
      release_t: now_t,
      release_wobble_deg: last_wobble,
    }
    // Initial spring-back tick: remaining = 1, so caller still sees last_wobble.
    return { ctx: next, wobble_delta: last_wobble, surprise_factor: 0 }
  }

  function init(): DragContext {
    return makeIdleCtx()
  }

  return { init, onDragStart, onDragEnd, tick }
}
