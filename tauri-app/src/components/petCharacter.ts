// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Pet character rendering (S6 — Live2D-replacement visual layer).
 *
 * Two render paths, both 100% original / zero-copyright:
 *   1. drawSpriteCharacter — draws an external PNG 立绘 (the "real"
 *      artwork path; drop a transparent-bg portrait at
 *      /assets/pet/character.png and it gets used automatically).
 *   2. drawProceduralCharacter — a Canvas2D chibi anime girl drawn
 *      entirely from paths, used as the placeholder until real artwork
 *      is supplied. Much more presentable than the old geometric cat.
 *
 * Both paths share the same "alive" motion: gentle vertical float +
 * breathing scale, and both consume pet-anim's mouthOpen + blink so the
 * character reacts to lip-sync / blink scheduling.
 */

export interface CharacterFrame {
  /** Logical draw-area width (CSS px, pre-DPR). */
  readonly w: number
  /** Logical draw-area height (CSS px, pre-DPR). */
  readonly h: number
  /** Animation time (ms, monotonic). */
  readonly t: number
  /** 0..1 mouth openness from pet-anim lip-sync. */
  readonly mouthOpen: number
  /** 0..1 eyelid close amount (0 = open, 1 = fully shut). */
  readonly blink: number
  /** Tier-1 互动整体变换（缺省=单位变换）。pivot = 脚底中心。 */
  readonly rotateDeg?: number
  readonly offsetX?: number
  readonly offsetY?: number
  readonly scaleX?: number
  readonly scaleY?: number
}

/** Shared float/breath transform so sprite + procedural feel identical. */
function applyAliveTransform(_ctx: CanvasRenderingContext2D, f: CharacterFrame): { cx: number; cy: number; scale: number } {
  const cx = f.w / 2
  // Sit the character a touch above vertical centre so the body has room.
  const floatY = Math.sin(f.t / 1400) * 6
  const cy = f.h * 0.5 + floatY
  // Breathing: subtle scale oscillation.
  const breath = 1 + Math.sin(f.t / 1800) * 0.012
  return { cx, cy, scale: breath }
}

/**
 * Draw an external sprite portrait, fitted into the draw area with a
 * margin, centred, with the shared float/breath motion. Keeps aspect
 * ratio. This is the path used once real artwork exists.
 */
export function drawSpriteCharacter(
  ctx: CanvasRenderingContext2D,
  img: HTMLImageElement,
  f: CharacterFrame,
): void {
  const { cx, cy, scale } = applyAliveTransform(ctx, f)
  const maxW = f.w * 0.9
  const maxH = f.h * 0.92
  const iw = img.naturalWidth || img.width || 1
  const ih = img.naturalHeight || img.height || 1
  const fit = Math.min(maxW / iw, maxH / ih)
  const drawW = iw * fit * scale
  const drawH = ih * fit * scale

  // Tier-1 互动变换（缺省 = 单位）。pivot = 脚底中心，旋转/挤压像"站着晃"。
  const rotateRad = ((f.rotateDeg ?? 0) * Math.PI) / 180
  const sx = f.scaleX ?? 1
  const sy = f.scaleY ?? 1
  const ox = f.offsetX ?? 0
  const oy = f.offsetY ?? 0
  const footX = cx + ox
  const footY = cy + drawH / 2 + oy

  ctx.save()
  ctx.imageSmoothingEnabled = true
  ctx.imageSmoothingQuality = 'high'
  ctx.translate(footX, footY)
  ctx.rotate(rotateRad)
  ctx.scale(sx, sy)
  ctx.drawImage(img, -drawW / 2, -drawH, drawW, drawH)
  ctx.restore()
}

/**
 * Procedural chibi anime girl placeholder — drawn from scratch with
 * Canvas2D paths. Purple-themed to echo the DeskPet brand. Reacts to
 * blink + mouthOpen. Replace by dropping a real PNG at
 * /assets/pet/character.png.
 */
export function drawProceduralCharacter(
  ctx: CanvasRenderingContext2D,
  f: CharacterFrame,
): void {
  const { cx, cy, scale } = applyAliveTransform(ctx, f)
  // Character is authored in a ~260x360 local space; scale to fit area.
  const base = Math.min(f.w / 280, f.h / 380, 1.6)
  ctx.save()
  ctx.translate(cx, cy)
  ctx.scale(base * scale, base * scale)

  // ---- soft contact shadow ----
  ctx.fillStyle = 'rgba(80, 60, 120, 0.18)'
  ctx.beginPath()
  ctx.ellipse(0, 178, 78, 16, 0, 0, Math.PI * 2)
  ctx.fill()

  // ---- back hair (behind body) ----
  ctx.fillStyle = '#5b4a86'
  ctx.beginPath()
  ctx.moveTo(-78, -40)
  ctx.quadraticCurveTo(-96, 90, -64, 150)
  ctx.quadraticCurveTo(0, 120, 64, 150)
  ctx.quadraticCurveTo(96, 90, 78, -40)
  ctx.quadraticCurveTo(0, -90, -78, -40)
  ctx.closePath()
  ctx.fill()

  // ---- dress / body ----
  const dress = ctx.createLinearGradient(0, 60, 0, 180)
  dress.addColorStop(0, '#9d8bd8')
  dress.addColorStop(1, '#7a68c0')
  ctx.fillStyle = dress
  ctx.beginPath()
  ctx.moveTo(-30, 64)
  ctx.lineTo(30, 64)
  ctx.quadraticCurveTo(64, 150, 52, 176)
  ctx.lineTo(-52, 176)
  ctx.quadraticCurveTo(-64, 150, -30, 64)
  ctx.closePath()
  ctx.fill()
  // collar
  ctx.fillStyle = '#ffffff'
  ctx.beginPath()
  ctx.moveTo(-22, 60)
  ctx.lineTo(22, 60)
  ctx.lineTo(0, 84)
  ctx.closePath()
  ctx.fill()

  // ---- neck ----
  ctx.fillStyle = '#ffe1d2'
  ctx.fillRect(-12, 38, 24, 28)

  // ---- head (face) ----
  const skin = ctx.createLinearGradient(0, -70, 0, 40)
  skin.addColorStop(0, '#ffeadd')
  skin.addColorStop(1, '#ffd9c4')
  ctx.fillStyle = skin
  ctx.beginPath()
  ctx.ellipse(0, -16, 56, 62, 0, 0, Math.PI * 2)
  ctx.fill()

  // ---- ears hint ----
  ctx.fillStyle = '#ffd9c4'
  ctx.beginPath(); ctx.ellipse(-54, -8, 9, 14, 0, 0, Math.PI * 2); ctx.fill()
  ctx.beginPath(); ctx.ellipse(54, -8, 9, 14, 0, 0, Math.PI * 2); ctx.fill()

  // ---- blush ----
  ctx.fillStyle = 'rgba(255, 150, 160, 0.4)'
  ctx.beginPath(); ctx.ellipse(-30, 4, 13, 8, 0, 0, Math.PI * 2); ctx.fill()
  ctx.beginPath(); ctx.ellipse(30, 4, 13, 8, 0, 0, Math.PI * 2); ctx.fill()

  // ---- eyes (react to blink) ----
  const eyeOpen = 1 - Math.min(1, Math.max(0, f.blink))
  drawEye(ctx, -24, -12, eyeOpen)
  drawEye(ctx, 24, -12, eyeOpen)

  // ---- eyebrows ----
  ctx.strokeStyle = '#7a5c4a'
  ctx.lineWidth = 2.4
  ctx.lineCap = 'round'
  ctx.beginPath(); ctx.moveTo(-36, -34); ctx.quadraticCurveTo(-24, -38, -12, -34); ctx.stroke()
  ctx.beginPath(); ctx.moveTo(12, -34); ctx.quadraticCurveTo(24, -38, 36, -34); ctx.stroke()

  // ---- nose hint ----
  ctx.strokeStyle = 'rgba(200,150,130,0.5)'
  ctx.lineWidth = 1.6
  ctx.beginPath(); ctx.moveTo(0, 2); ctx.lineTo(-3, 8); ctx.stroke()

  // ---- mouth (reacts to mouthOpen) ----
  const m = Math.min(1, Math.max(0, f.mouthOpen))
  ctx.fillStyle = '#c4566a'
  ctx.strokeStyle = '#b04a5e'
  ctx.lineWidth = 2
  if (m > 0.08) {
    ctx.beginPath()
    ctx.ellipse(0, 20, 8, 3 + m * 9, 0, 0, Math.PI * 2)
    ctx.fill()
  } else {
    ctx.beginPath()
    ctx.moveTo(-8, 18)
    ctx.quadraticCurveTo(0, 24, 8, 18)
    ctx.stroke()
  }

  // ---- front hair / bangs (over forehead) ----
  const hair = ctx.createLinearGradient(0, -80, 0, 0)
  hair.addColorStop(0, '#7c6ab0')
  hair.addColorStop(1, '#5b4a86')
  ctx.fillStyle = hair
  ctx.beginPath()
  ctx.moveTo(-60, -20)
  ctx.quadraticCurveTo(-66, -78, 0, -84)
  ctx.quadraticCurveTo(66, -78, 60, -20)
  // bangs sweep
  ctx.quadraticCurveTo(40, -44, 22, -30)
  ctx.quadraticCurveTo(10, -50, -2, -32)
  ctx.quadraticCurveTo(-14, -50, -26, -30)
  ctx.quadraticCurveTo(-44, -46, -60, -20)
  ctx.closePath()
  ctx.fill()
  // hair shine
  ctx.fillStyle = 'rgba(255,255,255,0.18)'
  ctx.beginPath()
  ctx.ellipse(-20, -56, 22, 9, -0.4, 0, Math.PI * 2)
  ctx.fill()

  ctx.restore()
}

function drawEye(ctx: CanvasRenderingContext2D, x: number, y: number, open: number): void {
  ctx.save()
  ctx.translate(x, y)
  if (open < 0.12) {
    // closed: a gentle lash line
    ctx.strokeStyle = '#5a4636'
    ctx.lineWidth = 2.4
    ctx.lineCap = 'round'
    ctx.beginPath(); ctx.moveTo(-11, 0); ctx.quadraticCurveTo(0, 5, 11, 0); ctx.stroke()
    ctx.restore()
    return
  }
  const eh = 17 * open
  // white
  ctx.fillStyle = '#ffffff'
  ctx.beginPath(); ctx.ellipse(0, 0, 12, eh, 0, 0, Math.PI * 2); ctx.fill()
  // iris (purple)
  const iris = ctx.createRadialGradient(0, 2, 1, 0, 2, 10)
  iris.addColorStop(0, '#8a6fd0')
  iris.addColorStop(1, '#4a3a82')
  ctx.fillStyle = iris
  ctx.beginPath(); ctx.ellipse(0, 1, 8, Math.max(2, eh * 0.85), 0, 0, Math.PI * 2); ctx.fill()
  // pupil
  ctx.fillStyle = '#2a2140'
  ctx.beginPath(); ctx.ellipse(0, 2, 4, Math.max(1.5, eh * 0.5), 0, 0, Math.PI * 2); ctx.fill()
  // highlight
  ctx.fillStyle = 'rgba(255,255,255,0.95)'
  ctx.beginPath(); ctx.ellipse(-3, -3, 3, 3.5, 0, 0, Math.PI * 2); ctx.fill()
  ctx.beginPath(); ctx.ellipse(3, 3, 1.5, 1.5, 0, 0, Math.PI * 2); ctx.fill()
  // upper lash line
  ctx.strokeStyle = '#3a2c4a'
  ctx.lineWidth = 2
  ctx.lineCap = 'round'
  ctx.beginPath(); ctx.ellipse(0, 0, 12, eh, 0, Math.PI * 1.05, Math.PI * 1.95); ctx.stroke()
  ctx.restore()
}

/**
 * Try to load an external sprite portrait. Resolves to the loaded image
 * or null if it 404s / errors (so the caller falls back to procedural).
 * Pure browser API; safe to call once at startCanvas2D init.
 */
export function loadSpriteImage(url: string): Promise<HTMLImageElement | null> {
  return new Promise((resolve) => {
    try {
      const img = new Image()
      img.onload = () => resolve(img.naturalWidth > 0 ? img : null)
      img.onerror = () => resolve(null)
      img.src = url
    } catch {
      resolve(null)
    }
  })
}
