// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * PetCelebrationBubble (pet-anim/UI) — C3 / D2 共用气泡。
 *
 * Shows a translucent rounded bubble above the pet for `duration_ms`, then
 * auto-dismisses. Used by:
 *   - C3 hourly / anniversary triggers (PRD §3 C3)
 *   - D2 milestone celebrations (PRD §3 D2)
 *   - C2 long-absence welcome (PRD §3 C2, intensity=bubble/intense)
 *
 * The component is "controlled" — App.tsx owns visibility / message state.
 */
import { useEffect } from 'react'

export interface PetCelebrationBubbleProps {
  visible: boolean
  message: string
  duration_ms?: number
  onDismiss?: () => void
  /** Optional position offset from default (above pet's face). */
  offset?: { x?: number; y?: number }
}

export function PetCelebrationBubble({
  visible,
  message,
  duration_ms = 3000,
  onDismiss,
  offset,
}: PetCelebrationBubbleProps): React.ReactElement | null {
  useEffect(() => {
    if (!visible) return
    if (duration_ms <= 0) return
    const handle = window.setTimeout(() => {
      onDismiss?.()
    }, duration_ms)
    return () => window.clearTimeout(handle)
  }, [visible, message, duration_ms, onDismiss])

  if (!visible || !message) return null

  return (
    <div
      data-testid="pet-celebration-bubble"
      style={{
        position: 'fixed',
        right: 32 + (offset?.x ?? 0),
        bottom: 220 + (offset?.y ?? 0),
        zIndex: 30,
        padding: '8px 14px',
        background: 'rgba(30, 36, 48, 0.86)',
        color: '#f9fafb',
        borderRadius: 14,
        fontSize: 14,
        fontFamily: 'inherit',
        boxShadow: '0 6px 18px rgba(0,0,0,0.25)',
        backdropFilter: 'blur(6px)',
        pointerEvents: 'none',
        animation: 'pet-bubble-pop 240ms cubic-bezier(0.2, 0.8, 0.2, 1)',
        maxWidth: 220,
        lineHeight: 1.4,
      }}
    >
      {message}
    </div>
  )
}
