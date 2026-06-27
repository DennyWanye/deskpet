// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * gazeTracking.ts — TDD §2.4 / §3.4 (v3 unit-conversion patch).
 *
 * Maps a CSS-pixel pointer position to (head_yaw_deg, head_pitch_deg,
 * eye_yaw_norm, eye_pitch_norm). The conversion pipeline:
 *
 *   1. Centre the pointer at `face_center_x/y` (CSS px, injected via
 *      `setFaceFrame`; the overlay must refresh on window/model
 *      resize — see PRD §6.0 ResizeObserver + same-source double-write).
 *   2. Normalise by `face_radius_css` so the input is roughly in [-1, +1]
 *      at the edges of the face bbox.
 *   3. atan2 → degrees, clamp to ±yaw_max_deg / ±pitch_max_deg.
 *   4. Deadzone: if the target differs from the previously smoothed value
 *      by less than `deadzone_deg`, snap the target back to the previous
 *      smoothed value (kills jitter for stationary cursors).
 *   5. First-order lowpass: smoothed = α·target + (1-α)·prev_smoothed.
 *   6. Head follows eye at `head_follow_ratio` (default 0.4).
 *   7. Eye output is converted from degrees → normalised [-1, +1] using
 *      probe_constants.eyeball_max_* so callers can ADD directly to
 *      ParamEyeBallX/Y (Cubism 4 EyeBall is normalised, not degrees —
 *      TDD §3.4 v3 patch).
 *
 * Idle recentre: when no target has been set in `idle_recenter_ms`,
 * the smoothed value decays towards 0 (so the pet looks "forward"
 * after the user walks away).
 *
 * Pure / DOM-free / time-injected.
 */
import { PROBE_CONSTANTS } from './_probe_constants'

export interface GazeOpts {
  /** Max yaw clamp in degrees. Default 20°. */
  yaw_max_deg?: number
  /** Max pitch clamp in degrees. Default 15°. */
  pitch_max_deg?: number
  /** Deadzone radius in degrees. Default 5°. */
  deadzone_deg?: number
  /** First-order lowpass alpha. Default 0.15. */
  lowpass_alpha?: number
  /** No-input duration before idle recentre kicks in. Default 10 s. */
  idle_recenter_ms?: number
  /** Head follows eye proportionally. Default 0.4. */
  head_follow_ratio?: number
  /** EyeBall norm bounds (overrideable from probe constants). */
  eyeball_max_x?: number
  eyeball_max_y?: number
}

export interface GazeState {
  target_yaw_deg: number
  target_pitch_deg: number
  smoothed_yaw_deg: number
  smoothed_pitch_deg: number
  last_input_t: number
  has_target: boolean
  face_center_x: number
  face_center_y: number
  face_radius_css: number
}

export interface GazeTickResult {
  state: GazeState
  head_yaw_deg: number
  head_pitch_deg: number
  eye_yaw_norm: number
  eye_pitch_norm: number
}

export interface GazeTracker {
  init(): GazeState
  setFaceFrame(
    state: GazeState,
    cx: number,
    cy: number,
    radius_css: number,
  ): GazeState
  setTarget(state: GazeState, clientX: number, clientY: number, now_t: number): GazeState
  clearTarget(state: GazeState, now_t: number): GazeState
  tick(state: GazeState, now_t: number): GazeTickResult
}

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v
}

export function createGazeTracker(opts: GazeOpts = {}): GazeTracker {
  const yaw_max_deg = opts.yaw_max_deg ?? 20
  const pitch_max_deg = opts.pitch_max_deg ?? 15
  const deadzone_deg = opts.deadzone_deg ?? 5
  const lowpass_alpha = opts.lowpass_alpha ?? 0.15
  const idle_recenter_ms = opts.idle_recenter_ms ?? 10_000
  const head_follow_ratio = opts.head_follow_ratio ?? 0.4
  const eyeball_max_x = opts.eyeball_max_x ?? PROBE_CONSTANTS.eyeball_max_x
  const eyeball_max_y = opts.eyeball_max_y ?? PROBE_CONSTANTS.eyeball_max_y

  return {
    init(): GazeState {
      return {
        target_yaw_deg: 0,
        target_pitch_deg: 0,
        smoothed_yaw_deg: 0,
        smoothed_pitch_deg: 0,
        last_input_t: -Infinity,
        has_target: false,
        face_center_x: 0,
        face_center_y: 0,
        face_radius_css: 200, // safe default; should be overridden ASAP
      }
    },

    setFaceFrame(state, cx, cy, radius_css) {
      return {
        ...state,
        face_center_x: cx,
        face_center_y: cy,
        // Guard against zero/negative radius from a degenerate ResizeObserver
        // event (e.g. window minimised → 0×0 bbox).
        face_radius_css: Math.max(1, radius_css),
      }
    },

    setTarget(state, clientX, clientY, now_t) {
      const dx = clientX - state.face_center_x
      const dy = clientY - state.face_center_y
      const nx = dx / state.face_radius_css
      const ny = dy / state.face_radius_css
      // atan2(nx, 1) keeps sign correct for negative clientX (multi-display
      // secondary monitors to the left of primary).
      const raw_yaw = Math.atan2(nx, 1) * (180 / Math.PI)
      const raw_pitch = Math.atan2(ny, 1) * (180 / Math.PI)
      let target_yaw = clamp(raw_yaw, -yaw_max_deg, yaw_max_deg)
      let target_pitch = clamp(raw_pitch, -pitch_max_deg, pitch_max_deg)
      // Deadzone vs previous smoothed.
      if (Math.abs(target_yaw - state.smoothed_yaw_deg) < deadzone_deg) {
        target_yaw = state.smoothed_yaw_deg
      }
      if (Math.abs(target_pitch - state.smoothed_pitch_deg) < deadzone_deg) {
        target_pitch = state.smoothed_pitch_deg
      }
      return {
        ...state,
        target_yaw_deg: target_yaw,
        target_pitch_deg: target_pitch,
        last_input_t: now_t,
        has_target: true,
      }
    },

    clearTarget(state, now_t) {
      return {
        ...state,
        target_yaw_deg: 0,
        target_pitch_deg: 0,
        last_input_t: now_t,
        has_target: false,
      }
    },

    tick(state, now_t): GazeTickResult {
      // Decide target: if input went stale, drift to 0 (idle recentre).
      let effective_target_yaw = state.target_yaw_deg
      let effective_target_pitch = state.target_pitch_deg
      const idle_age = now_t - state.last_input_t
      if (!state.has_target || idle_age >= idle_recenter_ms) {
        effective_target_yaw = 0
        effective_target_pitch = 0
      }
      const smoothed_yaw_deg =
        lowpass_alpha * effective_target_yaw +
        (1 - lowpass_alpha) * state.smoothed_yaw_deg
      const smoothed_pitch_deg =
        lowpass_alpha * effective_target_pitch +
        (1 - lowpass_alpha) * state.smoothed_pitch_deg

      const head_yaw_deg = smoothed_yaw_deg * head_follow_ratio
      const head_pitch_deg = smoothed_pitch_deg * head_follow_ratio
      // Degrees → normalised eye coordinates (Cubism 4 EyeBall = [-1, +1]).
      const eye_yaw_norm = (smoothed_yaw_deg / yaw_max_deg) * eyeball_max_x
      const eye_pitch_norm = (smoothed_pitch_deg / pitch_max_deg) * eyeball_max_y

      const next: GazeState = {
        ...state,
        smoothed_yaw_deg,
        smoothed_pitch_deg,
      }
      return {
        state: next,
        head_yaw_deg,
        head_pitch_deg,
        eye_yaw_norm,
        eye_pitch_norm,
      }
    },
  }
}
