// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * userInputObserver (pet-anim/B1) — Pet Animation UX v2.
 *
 * Pure FSM tracking whether the user is actively typing into the chat input
 * box, per PRD §3 B1 / TDD §2.2.
 *
 * Behaviour
 *   active = focused
 *            && !composing                                  (IME guard)
 *            && (now_t - last_input_t) < stop_after_idle_ms (trailing window)
 *
 * Event policy
 *   onFocus              → focused = true (does not set last_input_t)
 *   onBlur               → focused = false; active immediately drops to false
 *   onKeydown            → if composing → no-op; else last_input_t = now_t
 *   onCompositionStart   → composing = true (subsequent keydowns ignored)
 *   onCompositionEnd     → composing = false; last_input_t = now_t
 *                          (committed character counts as user input)
 *   tick(now_t)          → re-evaluate active flag against the trailing window
 *
 * NFR-6: every now_t is DOMHighResTimeStamp passed by caller — no internal
 * `performance.now()` / `Date.now()` / `setTimeout` calls. Caller drives
 * inactivity timeout via tick() from the animation frame.
 *
 * Pure-function style: state in, new state out.
 */

export interface UserInputOpts {
  /** Inactivity timeout in ms after last keystroke. PRD §3 B1: 1500. */
  stop_after_idle_ms?: number
  /** When true, keydowns during IME composition do not count. PRD §3 B1 IME. */
  ime_aware?: boolean
}

export interface UserInputState {
  focused: boolean
  composing: boolean
  active: boolean
  /** DOMHighResTimeStamp of last counted input event. -Infinity if never. */
  last_input_t: number
}

export interface UserInputObserver {
  init(): UserInputState
  onFocus(s: UserInputState, now_t: number): UserInputState
  onBlur(s: UserInputState, now_t: number): UserInputState
  onKeydown(s: UserInputState, now_t: number): UserInputState
  onCompositionStart(s: UserInputState, now_t: number): UserInputState
  onCompositionEnd(s: UserInputState, now_t: number): UserInputState
  /** Periodic re-evaluation against the trailing window. Idempotent. */
  tick(s: UserInputState, now_t: number): UserInputState
}

const DEFAULTS: Required<UserInputOpts> = {
  stop_after_idle_ms: 1500,
  ime_aware: true,
}

function safeNum(v: number | undefined, fallback: number, mustBePositive = false): number {
  if (v === undefined || !Number.isFinite(v)) return fallback
  if (mustBePositive && v <= 0) return fallback
  return v
}

function makeInitial(): UserInputState {
  return {
    focused: false,
    composing: false,
    active: false,
    last_input_t: -Infinity,
  }
}

function computeActive(
  s: Pick<UserInputState, 'focused' | 'composing' | 'last_input_t'>,
  now_t: number,
  opts: Required<UserInputOpts>,
): boolean {
  if (!s.focused) return false
  if (opts.ime_aware && s.composing) return false
  if (!Number.isFinite(s.last_input_t)) return false
  return now_t - s.last_input_t < opts.stop_after_idle_ms
}

export function createUserInputObserver(
  rawOpts: UserInputOpts = {},
  onChange?: (active: boolean, now_t: number) => void,
): UserInputObserver {
  const opts: Required<UserInputOpts> = {
    stop_after_idle_ms: safeNum(rawOpts.stop_after_idle_ms, DEFAULTS.stop_after_idle_ms, true),
    ime_aware: rawOpts.ime_aware ?? DEFAULTS.ime_aware,
  }

  function emit(prev_active: boolean, next: UserInputState, now_t: number): UserInputState {
    if (next.active !== prev_active && onChange) {
      // Defensive: callback failures must not crash the FSM.
      try {
        onChange(next.active, now_t)
      } catch {
        /* swallow — debug surface only */
      }
    }
    return next
  }

  function rebuild(s: UserInputState, now_t: number, patch: Partial<UserInputState>): UserInputState {
    const merged: UserInputState = { ...s, ...patch }
    merged.active = computeActive(merged, now_t, opts)
    return emit(s.active, merged, now_t)
  }

  function onFocus(s: UserInputState, now_t: number): UserInputState {
    if (!Number.isFinite(now_t)) return s
    if (s.focused) return s
    return rebuild(s, now_t, { focused: true })
  }

  function onBlur(s: UserInputState, now_t: number): UserInputState {
    if (!Number.isFinite(now_t)) return s
    if (!s.focused && !s.composing) return s
    // Blur cancels composition implicitly (best-effort; IME may still send composend later → idempotent).
    return rebuild(s, now_t, { focused: false, composing: false })
  }

  function onKeydown(s: UserInputState, now_t: number): UserInputState {
    if (!Number.isFinite(now_t)) return s
    if (opts.ime_aware && s.composing) {
      // During IME composition, intermediate keydowns are pinyin chars — don't count.
      return s
    }
    return rebuild(s, now_t, { last_input_t: now_t })
  }

  function onCompositionStart(s: UserInputState, now_t: number): UserInputState {
    if (!Number.isFinite(now_t)) return s
    if (s.composing) return s
    // Entering composition: caller is still focused; active drops because composing=true.
    return rebuild(s, now_t, { composing: true })
  }

  function onCompositionEnd(s: UserInputState, now_t: number): UserInputState {
    if (!Number.isFinite(now_t)) return s
    if (!s.composing) {
      // Stray end without start — at least treat as an input event if focused.
      if (s.focused) return rebuild(s, now_t, { last_input_t: now_t })
      return s
    }
    // The committed character counts as input — bump last_input_t so trailing
    // window starts here, then re-evaluate active.
    return rebuild(s, now_t, { composing: false, last_input_t: now_t })
  }

  function tick(s: UserInputState, now_t: number): UserInputState {
    if (!Number.isFinite(now_t)) return s
    const next_active = computeActive(s, now_t, opts)
    if (next_active === s.active) return s
    const next: UserInputState = { ...s, active: next_active }
    return emit(s.active, next, now_t)
  }

  return {
    init: makeInitial,
    onFocus,
    onBlur,
    onKeydown,
    onCompositionStart,
    onCompositionEnd,
    tick,
  }
}
