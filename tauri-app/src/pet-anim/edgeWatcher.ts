/**
 * edgeWatcher (pet-anim/E1) — Pet Animation UX v2.
 *
 * After a drag ends, decides whether the pet snapped close enough to a
 * screen edge to enter "edge-attached" pose (PRD §3 E1).
 *
 * Pure decision module:
 *   pickEdge(petRect, screen, threshold_px) → Edge | null
 *   poseForEdge(edge) → degrees for ParamAngleZ
 *
 * Caller (Live2DCanvas) is responsible for:
 *   - Calling Tauri setPosition() to snap the window if `Edge !== null`.
 *   - Forwarding the result to overlay.setEdgeAttached(edge).
 */

export type Edge = 'left' | 'right' | 'top' | 'bottom' | null

export interface Rect {
  x: number
  y: number
  w: number
  h: number
}

export interface ScreenBounds {
  width: number
  height: number
}

export interface EdgeOpts {
  edge_threshold_px?: number
}

const DEFAULTS: Required<EdgeOpts> = {
  edge_threshold_px: 100,
}

function safeNum(v: number | undefined, fallback: number, mustBePositive = false): number {
  if (v === undefined || !Number.isFinite(v)) return fallback
  if (mustBePositive && v <= 0) return fallback
  return v
}

export function pickEdge(petRect: Rect, screen: ScreenBounds, rawThreshold?: number): Edge {
  if (!petRect || !screen) return null
  if (!Number.isFinite(petRect.x) || !Number.isFinite(petRect.y)) return null
  if (!Number.isFinite(screen.width) || !Number.isFinite(screen.height)) return null

  const threshold = safeNum(rawThreshold, DEFAULTS.edge_threshold_px, true)
  const cx = petRect.x + petRect.w / 2
  const cy = petRect.y + petRect.h / 2

  // Find the closest edge that is within threshold; ties broken by smallest distance.
  const distances: Array<{ edge: Edge; d: number }> = [
    { edge: 'left', d: cx },
    { edge: 'right', d: screen.width - cx },
    { edge: 'top', d: cy },
    { edge: 'bottom', d: screen.height - cy },
  ]
  let best: { edge: Edge; d: number } | null = null
  for (const cand of distances) {
    if (cand.d > threshold) continue
    if (!best || cand.d < best.d) best = cand
  }
  return best ? best.edge : null
}

/**
 * ParamAngleZ rotation in degrees per pose:
 *   right  → +90° (右躺)
 *   left   → -90° (左躺)
 *   bottom → +180° (倒挂 — Hiyori 2D 视觉看起来仍站立但旋转 180)
 *   top    →   0° (正立靠顶)
 */
export function poseForEdge(edge: Edge): number {
  switch (edge) {
    case 'right':
      return 90
    case 'left':
      return -90
    case 'bottom':
      return 180
    case 'top':
      return 0
    default:
      return 0
  }
}

/**
 * Snap target: returns the position to setPosition() for the given edge.
 * Window vertically/horizontally centred on the same axis, offset 10px out
 * past the edge (PRD §3 E1 snap_offset_px=10).
 */
export function snapTarget(petRect: Rect, screen: ScreenBounds, edge: Edge, snap_offset_px = 10): { x: number; y: number } | null {
  if (edge === null) return null
  switch (edge) {
    case 'left':
      return { x: -snap_offset_px, y: petRect.y }
    case 'right':
      return { x: screen.width - petRect.w + snap_offset_px, y: petRect.y }
    case 'top':
      return { x: petRect.x, y: -snap_offset_px }
    case 'bottom':
      return { x: petRect.x, y: screen.height - petRect.h + snap_offset_px }
  }
}
