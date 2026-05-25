/**
 * PetDNDBadge (pet-anim/F1 UI) — ZZZ corner badge per PRD §3 F1 m-5 spec.
 *
 * Position: pet 窗 right top, offset (+8, -4) px
 * Size:     14×14
 * Color:    #94a3b8
 * Opacity:  0.4
 * Font:     emoji
 * z-index:  1 (lowest — don't steal interaction)
 * Pointer:  none
 *
 * Renders nothing when `visible=false`.
 */
import React from 'react'

export interface PetDNDBadgeProps {
  visible: boolean
  /** Optional override of the badge symbol. */
  symbol?: string
}

export function PetDNDBadge({ visible, symbol = '💤' }: PetDNDBadgeProps): React.ReactElement | null {
  if (!visible) return null
  return (
    <div
      data-testid="pet-dnd-badge"
      style={{
        position: 'fixed',
        top: -4,
        right: 8,
        width: 14,
        height: 14,
        opacity: 0.4,
        color: '#94a3b8',
        fontSize: 12,
        lineHeight: '14px',
        textAlign: 'center',
        fontFamily: 'system-ui, "Segoe UI Emoji", "Apple Color Emoji", sans-serif',
        pointerEvents: 'none',
        zIndex: 1,
      }}
      aria-label="勿扰模式"
    >
      {symbol}
    </div>
  )
}
