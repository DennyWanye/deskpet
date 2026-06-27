// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * funInteractions — 2026-05-31 业界最佳实践灵感的 12 项桌宠交互观察器。
 *
 * 设计哲学：每个观察器是一个**纯函数 + 自己的 context**，AnimationOverlay
 * 在 applyTo 里 tick 一次拿到当前帧的 delta，再叠加到 Hiyori param 上。
 * 这样既可单测（context-in → context-out + outputs），又不破 OverlaY 的
 * 写顺序合同（TDD §3.6）。
 *
 * Observers
 * =========
 * 1. dragKinematics      — 拖拽期间瞬时速度 + 累积位移 → squash/stretch + 倾斜
 * 2. dragSpringBack      — 释放后 350ms 弹性回正（heldStateMachine 之外的
 *                          物理感）—— 仅做 BustY 维度，不和 wobble 冲突
 * 3. tapBurst            — 800ms 窗口连点计数 → look_up / curious / blush / annoyed
 * 4. regionAwareTap      — 命中坐标 → 'head' | 'face' | 'torso' | 'legs'
 * 5. longPressPetting    — 持续按 ≥600ms 触发抚摸（Cheek + EyeSmile 渐增）
 * 6. cursorProximityLean — 光标 < 80px 时身体微微倾向（亲密感）
 * 7. cursorShyAway       — 光标在脸上快速划过（>1500px/s）→ 害羞退缩
 * 8. cursorCircleDizzy   — 光标 1.5s 内绕桌宠 ≥720° → 晕眩
 * 9. timeOfDayMood       — hour → perky / normal / sleepy（眨眼率 + 整体气场）
 * 10. idleFidget         — 90~150s 无交互随机插入小动作
 * 11. rapidDoubleTap     — <250ms 双击 → 惊讶（眼大 + 眉扬 + 抖一下）
 * 12. dragImpactTrail    — 拖拽方向相反的 HairFront 拖尾（被风吹的感觉）
 *
 * Conventions
 * ===========
 * - 所有 time/duration 单位 ms（与 AnimationOverlay 内部 now_t 一致 — performance.now()）
 * - delta 输出单位：BodyAngle/HeadAngle = 度；Cheek/EyeSmile/HairFront/BustY = 归一化 0..1
 * - 所有 observer 在 cold start (ctx 全 0) 时不输出任何 delta（零回归）
 */

// ─────────────────────────── Pure helpers ────────────────────────────

export function clamp(v: number, lo: number, hi: number): number {
  if (!Number.isFinite(v)) return lo;
  return Math.max(lo, Math.min(hi, v));
}

export function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * clamp(t, 0, 1);
}

/** Ease-out cubic — used for spring back, dizzy decay. */
export function easeOutCubic(t: number): number {
  const x = clamp(t, 0, 1);
  return 1 - Math.pow(1 - x, 3);
}

// ───────────────────────── 1. dragKinematics ─────────────────────────

export interface DragKinematicsCtx {
  /** ms timestamp of last pointer sample, NaN if no prior sample. */
  last_t: number;
  last_x: number;
  last_y: number;
  /** Filtered velocity (low-pass). px/sec. */
  vx: number;
  vy: number;
  /** Cumulative net displacement during the current hold (px). */
  cum_dx: number;
  cum_dy: number;
  /** True while pointer is down. */
  active: boolean;
}

export interface DragKinematicsOutput {
  /** ParamBustY delta in [-0.2..0.3]: positive = stretched up (being held),
   *  negative = squashed down (being released into the floor). */
  squash_delta: number;
  /** ParamBodyAngleX delta in [-8..8] degrees: lean opposite to drag dir
   *  so the body "trails" the head when yanked. */
  lean_delta_deg: number;
  /** ParamHairFront delta in [-1..1]: hair trails opposite drag direction. */
  hair_trail_delta: number;
}

export function createDragKinematicsCtx(): DragKinematicsCtx {
  return { last_t: NaN, last_x: 0, last_y: 0, vx: 0, vy: 0, cum_dx: 0, cum_dy: 0, active: false };
}

/** Feed a pointermove event during an active hold. Returns updated ctx. */
export function dragKinematicsUpdate(
  ctx: DragKinematicsCtx,
  x: number,
  y: number,
  now_t: number,
): DragKinematicsCtx {
  if (!ctx.active) {
    // Begin: snapshot, no delta yet.
    return { ...ctx, last_t: now_t, last_x: x, last_y: y, vx: 0, vy: 0, cum_dx: 0, cum_dy: 0, active: true };
  }
  const dt_ms = now_t - ctx.last_t;
  if (!Number.isFinite(dt_ms) || dt_ms <= 0) return ctx;
  const dx = x - ctx.last_x;
  const dy = y - ctx.last_y;
  // Instantaneous velocity (px/sec) then low-pass at alpha=0.4 to dampen jitter.
  const inst_vx = (dx / dt_ms) * 1000;
  const inst_vy = (dy / dt_ms) * 1000;
  const alpha = 0.4;
  return {
    ...ctx,
    last_t: now_t,
    last_x: x,
    last_y: y,
    vx: ctx.vx * (1 - alpha) + inst_vx * alpha,
    vy: ctx.vy * (1 - alpha) + inst_vy * alpha,
    cum_dx: ctx.cum_dx + dx,
    cum_dy: ctx.cum_dy + dy,
  };
}

/** Begin a new hold — reset kinematics. */
export function dragKinematicsBegin(_ctx: DragKinematicsCtx, x: number, y: number, now_t: number): DragKinematicsCtx {
  return { last_t: now_t, last_x: x, last_y: y, vx: 0, vy: 0, cum_dx: 0, cum_dy: 0, active: true };
}

/** End hold — mark inactive but preserve final velocity for spring-back. */
export function dragKinematicsEnd(ctx: DragKinematicsCtx): DragKinematicsCtx {
  return { ...ctx, active: false };
}

/** Read current frame's output. Returns zeros when inactive.
 *  2026-05-31 fun-ux: 幅度 ×2~3 让形变肉眼可见（之前太微妙）。 */
export function dragKinematicsSample(ctx: DragKinematicsCtx): DragKinematicsOutput {
  if (!ctx.active) return { squash_delta: 0, lean_delta_deg: 0, hair_trail_delta: 0 };
  // Squash: positive when being lifted (negative vy = moving up). 加大到 ±0.7。
  const vy_norm = clamp(-ctx.vy / 1500, -0.8, 1.2);
  const squash_delta = clamp(vy_norm * 0.6, -0.4, 0.7);
  // Lean: body trails horizontal drag. 加大到 ±18°。
  const lean_delta_deg = clamp(-ctx.vx / 120, -18, 18);
  // Hair trail: opposite x-velocity. 加大到 ±2.5。
  const hair_trail_delta = clamp(-ctx.vx / 500, -2.5, 2.5);
  return { squash_delta, lean_delta_deg, hair_trail_delta };
}

// ───────────────────────── 2. dragSpringBack (BustY only) ──────────────

export interface DragSpringBackCtx {
  /** ms when spring began; NaN if inactive. */
  start_t: number;
  start_squash: number;
  duration_ms: number;
}

export function createDragSpringBackCtx(): DragSpringBackCtx {
  return { start_t: NaN, start_squash: 0, duration_ms: 350 };
}

export function dragSpringBackBegin(starting_squash: number, now_t: number): DragSpringBackCtx {
  return { start_t: now_t, start_squash: starting_squash, duration_ms: 350 };
}

export function dragSpringBackSample(ctx: DragSpringBackCtx, now_t: number): { squash_delta: number; done: boolean } {
  if (!Number.isFinite(ctx.start_t)) return { squash_delta: 0, done: true };
  const t = (now_t - ctx.start_t) / ctx.duration_ms;
  if (t >= 1) return { squash_delta: 0, done: true };
  // Bounce overshoot: ease into a small negative dip then back to 0.
  // squash = start * (1 - easeOut(t)) + sin(π * t) * 0.05 * sign(start)
  const decay = ctx.start_squash * (1 - easeOutCubic(t));
  const bounce = Math.sin(Math.PI * t) * 0.05 * Math.sign(ctx.start_squash || 1);
  return { squash_delta: decay - bounce, done: false };
}

// ───────────────────────── 3. tapBurst ───────────────────────────────

export interface TapBurstCtx {
  taps: number[]; // timestamps within window
  window_ms: number;
}

export type TapBurstIntensity = 'look_up' | 'curious' | 'blush' | 'annoyed';

export function createTapBurstCtx(window_ms = 800): TapBurstCtx {
  return { taps: [], window_ms };
}

export function tapBurstAdd(ctx: TapBurstCtx, now_t: number): { ctx: TapBurstCtx; count: number; intensity: TapBurstIntensity } {
  const cutoff = now_t - ctx.window_ms;
  const surviving = ctx.taps.filter(t => t >= cutoff);
  surviving.push(now_t);
  const next: TapBurstCtx = { ...ctx, taps: surviving };
  const count = surviving.length;
  let intensity: TapBurstIntensity;
  if (count >= 5) intensity = 'annoyed';
  else if (count >= 3) intensity = 'blush';
  else if (count >= 2) intensity = 'curious';
  else intensity = 'look_up';
  return { ctx: next, count, intensity };
}

// ───────────────────────── 4. regionAwareTap ─────────────────────────

export type ClickRegion = 'head' | 'face' | 'torso' | 'legs' | 'unknown';

/** Pure function — given click coord (CSS px relative to viewport) + face
 *  frame, classify which body region was hit. Used to vary the reaction.
 *  - head:  top 20% of face_frame.height, +/- 1.5 face_radius from center
 *  - face:  next 25% (the actual face zone)
 *  - torso: next 45% below face
 *  - legs:  bottom 10%
 *  - unknown: outside frame
 */
export interface FaceFrameLike {
  left: number;
  top: number;
  width: number;
  height: number;
  face_center_y: number;
  face_radius_css: number;
}

export function regionAwareClassify(client_x: number, client_y: number, ff: FaceFrameLike): ClickRegion {
  if (
    client_x < ff.left ||
    client_x > ff.left + ff.width ||
    client_y < ff.top ||
    client_y > ff.top + ff.height
  ) {
    return 'unknown';
  }
  const rel_y = (client_y - ff.top) / ff.height;
  if (rel_y < 0.2) return 'head';
  if (rel_y < 0.45) return 'face';
  if (rel_y < 0.9) return 'torso';
  return 'legs';
}

// ───────────────────────── 5. longPressPetting ───────────────────────

export interface LongPressCtx {
  press_t: number; // NaN when not pressed
  threshold_ms: number;
}

export function createLongPressCtx(threshold_ms = 600): LongPressCtx {
  return { press_t: NaN, threshold_ms };
}

export function longPressBegin(ctx: LongPressCtx, now_t: number): LongPressCtx {
  return { ...ctx, press_t: now_t };
}

export function longPressEnd(ctx: LongPressCtx): LongPressCtx {
  return { ...ctx, press_t: NaN };
}

export interface LongPressOutput {
  /** True when pressed past threshold. */
  petting: boolean;
  /** 0..1 progress of pet intensity (caps at 1 after 2s of pressing). */
  intensity: number;
}

export function longPressSample(ctx: LongPressCtx, now_t: number): LongPressOutput {
  if (!Number.isFinite(ctx.press_t)) return { petting: false, intensity: 0 };
  const held_ms = now_t - ctx.press_t;
  if (held_ms < ctx.threshold_ms) return { petting: false, intensity: 0 };
  // Ramp from 0 → 1 over (threshold_ms .. threshold_ms + 1400).
  const ramp = clamp((held_ms - ctx.threshold_ms) / 1400, 0, 1);
  return { petting: true, intensity: ramp };
}

// ───────────────────────── 6. cursorProximityLean ────────────────────

export interface ProximityOutput {
  /** Lean amount in degrees added to ParamBodyAngleX, capped [-5..5]. */
  lean_delta_deg: number;
  /** Lean amount in degrees added to ParamAngleY (head pitch toward cursor). */
  head_pitch_delta_deg: number;
}

export function cursorProximityLean(
  cursor_x: number,
  cursor_y: number,
  face_cx: number,
  face_cy: number,
  near_threshold_px = 80,
): ProximityOutput {
  const dx = cursor_x - face_cx;
  const dy = cursor_y - face_cy;
  const dist = Math.sqrt(dx * dx + dy * dy);
  if (dist > near_threshold_px) return { lean_delta_deg: 0, head_pitch_delta_deg: 0 };
  // Strength inversely proportional to distance.
  const strength = 1 - dist / near_threshold_px; // 0..1
  // Lean toward cursor x; pitch toward cursor y.
  const lean_delta_deg = clamp((dx / near_threshold_px) * 5 * strength, -5, 5);
  const head_pitch_delta_deg = clamp(-(dy / near_threshold_px) * 4 * strength, -4, 4);
  return { lean_delta_deg, head_pitch_delta_deg };
}

// ───────────────────────── 7. cursorShyAway ──────────────────────────

export interface ShyAwayCtx {
  last_t: number;
  last_x: number;
  last_y: number;
  triggered_until_t: number;
}

export function createShyAwayCtx(): ShyAwayCtx {
  return { last_t: NaN, last_x: 0, last_y: 0, triggered_until_t: 0 };
}

export function shyAwayUpdate(
  ctx: ShyAwayCtx,
  cursor_x: number,
  cursor_y: number,
  face_cx: number,
  face_cy: number,
  face_radius: number,
  now_t: number,
  speed_threshold_px_per_s = 1500,
): { ctx: ShyAwayCtx; triggered: boolean } {
  if (!Number.isFinite(ctx.last_t)) {
    return { ctx: { ...ctx, last_t: now_t, last_x: cursor_x, last_y: cursor_y }, triggered: false };
  }
  const dt_ms = now_t - ctx.last_t;
  if (dt_ms <= 0) return { ctx, triggered: false };
  const dx = cursor_x - ctx.last_x;
  const dy = cursor_y - ctx.last_y;
  const speed = Math.sqrt(dx * dx + dy * dy) / dt_ms * 1000;
  const over_face = Math.sqrt((cursor_x - face_cx) ** 2 + (cursor_y - face_cy) ** 2) < face_radius * 1.2;
  const cooldown_active = now_t < ctx.triggered_until_t;
  const triggered = !cooldown_active && over_face && speed > speed_threshold_px_per_s;
  return {
    ctx: {
      ...ctx,
      last_t: now_t,
      last_x: cursor_x,
      last_y: cursor_y,
      // 2026-05-31 fun-ux: 害羞反应 400→800ms 更明显。
      triggered_until_t: triggered ? now_t + 800 : ctx.triggered_until_t,
    },
    triggered,
  };
}

// ───────────────────────── 8. cursorCircleDizzy ──────────────────────

export interface CircleDizzyCtx {
  /** Recent angular samples (rad) + timestamps. Capped at 120 entries. */
  samples: Array<{ t: number; angle: number }>;
  window_ms: number;
  total_angle_threshold_rad: number;
  /** When triggered, head spins until this timestamp. */
  spin_until_t: number;
}

export function createCircleDizzyCtx(): CircleDizzyCtx {
  return {
    samples: [],
    window_ms: 1500,
    total_angle_threshold_rad: 4 * Math.PI, // 2 full circles = 720°
    spin_until_t: 0,
  };
}

export function circleDizzyUpdate(
  ctx: CircleDizzyCtx,
  cursor_x: number,
  cursor_y: number,
  face_cx: number,
  face_cy: number,
  face_radius: number,
  now_t: number,
): { ctx: CircleDizzyCtx; triggered: boolean } {
  const dx = cursor_x - face_cx;
  const dy = cursor_y - face_cy;
  const dist = Math.sqrt(dx * dx + dy * dy);
  // Only count when within 1.5x face radius (orbiting close).
  if (dist > face_radius * 2.5) {
    return { ctx, triggered: false };
  }
  const angle = Math.atan2(dy, dx);
  const cutoff = now_t - ctx.window_ms;
  const surviving = ctx.samples.filter(s => s.t >= cutoff);
  surviving.push({ t: now_t, angle });
  // Cap at 120 entries.
  if (surviving.length > 120) surviving.shift();

  // Compute cumulative unwrapped angular displacement.
  let total = 0;
  for (let i = 1; i < surviving.length; i++) {
    let delta = surviving[i].angle - surviving[i - 1].angle;
    // Unwrap to (-π..π).
    if (delta > Math.PI) delta -= 2 * Math.PI;
    if (delta < -Math.PI) delta += 2 * Math.PI;
    total += delta;
  }
  const cooldown_active = now_t < ctx.spin_until_t;
  const triggered = !cooldown_active && Math.abs(total) >= ctx.total_angle_threshold_rad;
  return {
    ctx: {
      ...ctx,
      samples: triggered ? [] : surviving,
      spin_until_t: triggered ? now_t + 1500 : ctx.spin_until_t,
    },
    triggered,
  };
}

/** Sample dizzy spin output. Sinusoidal head shake over the remaining spin time. */
export function circleDizzySample(ctx: CircleDizzyCtx, now_t: number): { angle_z_delta: number; eye_x_delta: number } {
  if (now_t >= ctx.spin_until_t) return { angle_z_delta: 0, eye_x_delta: 0 };
  const t_remain = (ctx.spin_until_t - now_t) / 1500; // 0..1
  const decay = t_remain;
  // Fast oscillation 4 Hz × amplitude 10° decaying with remaining time.
  const wobble_deg = Math.sin((now_t - ctx.spin_until_t + 1500) * 0.025) * 10 * decay;
  return { angle_z_delta: wobble_deg, eye_x_delta: Math.cos((now_t - ctx.spin_until_t + 1500) * 0.03) * 0.4 * decay };
}

// ───────────────────────── 9. timeOfDayMood ──────────────────────────

export type TimeOfDayMood = 'perky' | 'normal' | 'sleepy';

export interface TimeOfDayOutput {
  mood: TimeOfDayMood;
  /** Blink rate Hz override hint. */
  blink_hz_hint: number;
  /** Cheek extra (perky 0.1, sleepy 0). */
  cheek_extra: number;
  /** Eye open multiplier (sleepy 0.7). */
  eye_open_mul: number;
}

export function timeOfDayMood(hour: number): TimeOfDayOutput {
  // Robust to NaN / out-of-range.
  if (!Number.isFinite(hour)) hour = 12;
  hour = ((hour % 24) + 24) % 24;
  if (hour >= 6 && hour < 10) {
    return { mood: 'perky', blink_hz_hint: 0.4, cheek_extra: 0.1, eye_open_mul: 1.0 };
  }
  if (hour >= 22 || hour < 5) {
    return { mood: 'sleepy', blink_hz_hint: 0.18, cheek_extra: 0.0, eye_open_mul: 0.7 };
  }
  return { mood: 'normal', blink_hz_hint: 0.3, cheek_extra: 0.0, eye_open_mul: 1.0 };
}

// ───────────────────────── 10. idleFidget ────────────────────────────

export type FidgetKind = 'yawn' | 'stretch' | 'look_around';

export interface IdleFidgetCtx {
  /** Last user interaction time. */
  last_interaction_t: number;
  /** When next fidget is scheduled. NaN = unscheduled. */
  next_t: number;
  /** Min/max idle gap before fidget. */
  min_gap_ms: number;
  max_gap_ms: number;
}

export function createIdleFidgetCtx(min_gap_ms = 90_000, max_gap_ms = 150_000): IdleFidgetCtx {
  return { last_interaction_t: 0, next_t: NaN, min_gap_ms, max_gap_ms };
}

export function idleFidgetMarkInteraction(ctx: IdleFidgetCtx, now_t: number): IdleFidgetCtx {
  return { ...ctx, last_interaction_t: now_t, next_t: NaN };
}

/** Returns a fidget kind if it's time to fidget; else null. Caller plays the motion. */
export function idleFidgetMaybeTrigger(
  ctx: IdleFidgetCtx,
  now_t: number,
  rand: () => number = Math.random,
): { ctx: IdleFidgetCtx; trigger: FidgetKind | null } {
  // First call after an interaction: schedule next.
  if (!Number.isFinite(ctx.next_t) && ctx.last_interaction_t > 0) {
    const gap = ctx.min_gap_ms + rand() * (ctx.max_gap_ms - ctx.min_gap_ms);
    return { ctx: { ...ctx, next_t: ctx.last_interaction_t + gap }, trigger: null };
  }
  if (Number.isFinite(ctx.next_t) && now_t >= ctx.next_t) {
    const choices: FidgetKind[] = ['yawn', 'stretch', 'look_around'];
    const trigger = choices[Math.floor(rand() * choices.length) % choices.length];
    // Reschedule.
    const gap = ctx.min_gap_ms + rand() * (ctx.max_gap_ms - ctx.min_gap_ms);
    return { ctx: { ...ctx, next_t: now_t + gap }, trigger };
  }
  return { ctx, trigger: null };
}

// ───────────────────────── 11. rapidDoubleTap ────────────────────────

export interface RapidDoubleTapCtx {
  last_tap_t: number;
  window_ms: number;
  /** Surprise active until this timestamp. */
  surprise_until_t: number;
}

export function createRapidDoubleTapCtx(window_ms = 250): RapidDoubleTapCtx {
  return { last_tap_t: 0, window_ms, surprise_until_t: 0 };
}

export function rapidDoubleTapAdd(
  ctx: RapidDoubleTapCtx,
  now_t: number,
): { ctx: RapidDoubleTapCtx; triggered: boolean } {
  const gap = now_t - ctx.last_tap_t;
  const triggered = ctx.last_tap_t > 0 && gap > 0 && gap <= ctx.window_ms;
  return {
    ctx: {
      ...ctx,
      last_tap_t: now_t,
      // 2026-05-31 fun-ux: 惊讶持续 600→1100ms 让用户看清。
      surprise_until_t: triggered ? now_t + 1100 : ctx.surprise_until_t,
    },
    triggered,
  };
}

export function rapidDoubleTapSample(ctx: RapidDoubleTapCtx, now_t: number): { factor: number } {
  if (now_t >= ctx.surprise_until_t) return { factor: 0 };
  const remain = (ctx.surprise_until_t - now_t) / 1100;
  return { factor: clamp(remain, 0, 1) };
}

// ───────────────────────── Bundle (single tick API) ───────────────────

/** Aggregated context for the AnimationOverlay's per-frame consumer. */
export interface FunInteractionCtx {
  drag: DragKinematicsCtx;
  spring: DragSpringBackCtx;
  shy: ShyAwayCtx;
  circle: CircleDizzyCtx;
  long: LongPressCtx;
  fidget: IdleFidgetCtx;
  rapid: RapidDoubleTapCtx;
  /** Cached time-of-day mood; recomputed every 60s in tick. */
  time_mood: TimeOfDayOutput;
  time_mood_at_t: number;
}

export function createFunInteractionCtx(): FunInteractionCtx {
  return {
    drag: createDragKinematicsCtx(),
    spring: createDragSpringBackCtx(),
    shy: createShyAwayCtx(),
    circle: createCircleDizzyCtx(),
    long: createLongPressCtx(),
    fidget: createIdleFidgetCtx(),
    rapid: createRapidDoubleTapCtx(),
    time_mood: timeOfDayMood(12),
    time_mood_at_t: 0,
  };
}
