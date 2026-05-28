// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Day-0 Probe-2 output (TDD §0 Probe-2).
 *
 * Hiyori.cdi3.json confirms all required parameters exist (ParamAngleX/Y/Z,
 * ParamBodyAngleX/Y/Z, ParamEyeLOpen/ROpen, ParamEyeBallX/Y, ParamMouthOpenY).
 * The .moc3 binary holds the actual Min/Max ranges; for Cubism 4 stock models
 * the conventional ranges are encoded below as defaults.
 *
 * If a runtime probe ever discovers different ranges for a particular model,
 * adjust these constants — every consumer (saccadeScheduler, gazeTracking
 * unit conversion, AnimationOverlay clamping) reads from here so the change
 * propagates uniformly.
 *
 * NOTE: These are the **CSS-side / parameter-side** units the overlay writes.
 * Live2D itself clamps to the moc3's authoritative range, so writing slightly
 * outside is silently saturated rather than producing artifacts.
 */
export const PROBE_CONSTANTS = {
  // EyeBallX/Y are normalised [-1, +1] in stock Cubism 4 rigs (Hiyori
  // included). Saccade amplitude (FR-3) and gaze eye-channel norm
  // conversion (TDD §3.4 v3 patch) multiply against these.
  eyeball_max_x: 1.0,
  eyeball_max_y: 1.0,

  // Head angle is in degrees, ±30° is the conventional Cubism 4 range.
  // We clamp to ±20° yaw / ±15° pitch in gazeTracking (PRD FR-4) to
  // stay well inside the rig's safe envelope.
  angle_x_max_deg: 30,
  angle_y_max_deg: 30,
  angle_z_max_deg: 30,

  // Body angle (Perlin micro-motion target) — same convention.
  body_angle_x_max_deg: 10,
} as const

export type ProbeConstants = typeof PROBE_CONSTANTS
