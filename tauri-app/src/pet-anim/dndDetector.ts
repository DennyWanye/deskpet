// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * dndDetector (pet-anim/F1) — Pet Animation UX v2.
 *
 * Composes 3 DND triggers (PRD §3 F1):
 *   - fullscreen   — foreground window is fullscreen (Tauri Rust command)
 *   - typing       — keyboard input rate > 250 KPM over a 3-min window (M-19)
 *   - call         — any process has active audio capture (generic Win32
 *                    Audio Session API; not hard-coded to Teams/Zoom — M-20)
 *
 * Each trigger can be individually disabled by `enabled_triggers` so users
 * who never want DND from typing can flip just that off. If a trigger's
 * underlying API fails it auto-disables (audio-session API throws → call
 * detection silently turns off; other 2 keep working — PRD §3 F1 降级).
 *
 * Public API
 *   init(): DNDState
 *   notifyKeyEvent(state, now_t): DNDState
 *   tick(state, now_t): Promise<DNDState>
 *
 * The caller drives tick() from a ~2s interval (default
 * fullscreen_check_interval_ms). The KPM window slides via notifyKeyEvent
 * timestamps stored in a ring buffer.
 *
 * AC-10-03 guarantee: when DND is active, callers must still allow the pet
 * supervisor to surface red severity alerts. The dndDetector emits the
 * active+reasons set; precedence over red alert is enforced in App.tsx /
 * Live2DCanvas wiring, not here.
 */

export type DNDReason = 'fullscreen' | 'typing' | 'call'

export interface DNDOpts {
  fullscreen_check_interval_ms?: number
  typing_kpm_threshold?: number
  typing_window_ms?: number
  call_check_interval_ms?: number
  enabled_triggers?: DNDReason[]
}

export interface DNDState {
  active: boolean
  reasons: DNDReason[]
  /** Sliding window of recent key event timestamps (head = oldest). */
  key_event_ts: number[]
  /** Failed/disabled trigger set (audio API failed, etc.). */
  disabled: Set<DNDReason>
}

export interface DNDCallbacks {
  fetchFullscreen: () => Promise<boolean>
  fetchCallActive: () => Promise<boolean>
  onChange?: (active: boolean, reasons: DNDReason[], now_t: number) => void
}

export interface DNDDetector {
  init(): DNDState
  notifyKeyEvent(s: DNDState, now_t: number): DNDState
  tick(s: DNDState, now_t: number): Promise<DNDState>
}

const DEFAULTS: Required<Omit<DNDOpts, 'enabled_triggers'>> & { enabled_triggers: DNDReason[] } = {
  fullscreen_check_interval_ms: 2000,
  typing_kpm_threshold: 250,
  typing_window_ms: 180_000,
  call_check_interval_ms: 5000,
  enabled_triggers: ['fullscreen', 'typing', 'call'],
}

function safeNum(v: number | undefined, fallback: number, mustBePositive = false): number {
  if (v === undefined || !Number.isFinite(v)) return fallback
  if (mustBePositive && v <= 0) return fallback
  return v
}

function arraysEqual(a: DNDReason[], b: DNDReason[]): boolean {
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false
  return true
}

export function createDNDDetector(
  rawOpts: DNDOpts,
  callbacks: DNDCallbacks,
): DNDDetector {
  const opts = {
    fullscreen_check_interval_ms: safeNum(
      rawOpts.fullscreen_check_interval_ms,
      DEFAULTS.fullscreen_check_interval_ms,
      true,
    ),
    typing_kpm_threshold: safeNum(rawOpts.typing_kpm_threshold, DEFAULTS.typing_kpm_threshold, true),
    typing_window_ms: safeNum(rawOpts.typing_window_ms, DEFAULTS.typing_window_ms, true),
    call_check_interval_ms: safeNum(
      rawOpts.call_check_interval_ms,
      DEFAULTS.call_check_interval_ms,
      true,
    ),
    enabled_triggers: Array.isArray(rawOpts.enabled_triggers)
      ? rawOpts.enabled_triggers
      : DEFAULTS.enabled_triggers,
  }

  function init(): DNDState {
    return {
      active: false,
      reasons: [],
      key_event_ts: [],
      disabled: new Set(),
    }
  }

  /**
   * Slide the key event ring buffer: keep timestamps newer than (now − window).
   * Returns a new DNDState (immutable).
   */
  function notifyKeyEvent(s: DNDState, now_t: number): DNDState {
    if (!Number.isFinite(now_t)) return s
    const cutoff = now_t - opts.typing_window_ms
    const trimmed = s.key_event_ts.filter((t) => t >= cutoff)
    trimmed.push(now_t)
    return { ...s, key_event_ts: trimmed }
  }

  function computeKPM(state: DNDState, now_t: number): number {
    const cutoff = now_t - opts.typing_window_ms
    const recent = state.key_event_ts.filter((t) => t >= cutoff).length
    // KPM = recent events × (60_000 / window_ms)
    return (recent * 60_000) / opts.typing_window_ms
  }

  async function tick(s: DNDState, now_t: number): Promise<DNDState> {
    if (!Number.isFinite(now_t)) return s

    const reasons: DNDReason[] = []
    let next_disabled = new Set(s.disabled)

    // Fullscreen
    if (opts.enabled_triggers.includes('fullscreen') && !s.disabled.has('fullscreen')) {
      try {
        const fs = await callbacks.fetchFullscreen()
        if (fs) reasons.push('fullscreen')
      } catch {
        next_disabled.add('fullscreen')
      }
    }

    // Typing — purely from key_event_ts ring, no async needed.
    if (opts.enabled_triggers.includes('typing') && !s.disabled.has('typing')) {
      if (computeKPM(s, now_t) >= opts.typing_kpm_threshold) {
        reasons.push('typing')
      }
    }

    // Call
    if (opts.enabled_triggers.includes('call') && !s.disabled.has('call')) {
      try {
        const ca = await callbacks.fetchCallActive()
        if (ca) reasons.push('call')
      } catch {
        next_disabled.add('call')
      }
    }

    const next_active = reasons.length > 0
    if (next_active !== s.active || !arraysEqual(reasons, s.reasons) || next_disabled.size !== s.disabled.size) {
      const next: DNDState = {
        ...s,
        active: next_active,
        reasons,
        disabled: next_disabled,
      }
      if (callbacks.onChange) {
        try {
          callbacks.onChange(next_active, reasons, now_t)
        } catch {
          /* swallow */
        }
      }
      return next
    }
    return s
  }

  return { init, notifyKeyEvent, tick }
}
