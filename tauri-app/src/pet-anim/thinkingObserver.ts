// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * thinkingObserver (pet-anim/B2) — Pet Animation UX v2.
 *
 * Tracks whether the LLM is currently "thinking" (request in-flight, no chunk
 * yet) per PRD §3 B2 / TDD §2.3. Exits on:
 *   (a) first stream chunk arrival     → notifyFirstChunk    (M-1)
 *   (b) end of stream / cancel         → notifyEnd
 *   (c) max_duration_ms safety cutoff  → tick (PRD: 90s default, up from v1 30s)
 *
 * NFR-6: every now_t is DOMHighResTimeStamp passed by the caller.
 *
 * Stateful (caller owns one instance per chat session). State is encapsulated;
 * callers query via isActive(now_t) or are notified through the optional
 * onChange callback.
 */

export interface ThinkingOpts {
  /** Safety cutoff in ms after notifyStart. Default 90000 (M-1: v1 was 30000). */
  max_duration_ms?: number
}

export interface ThinkingObserver {
  /** Mark the start of an LLM request. Resets the safety timer. */
  notifyStart(now_t: number): void
  /** First chunk arrived — exit thinking (M-1 v2 behaviour, not chat_v2_final). */
  notifyFirstChunk(now_t: number): void
  /** End of stream, cancel, or other terminal — exit thinking. */
  notifyEnd(now_t: number): void
  /** Drive safety cutoff. Returns the current active flag. */
  tick(now_t: number): boolean
  isActive(now_t: number): boolean
  /** Debug snapshot for instrumentation. */
  debug(): {
    active: boolean
    start_t: number
  }
}

const DEFAULTS: Required<ThinkingOpts> = {
  max_duration_ms: 90_000,
}

function safeNum(v: number | undefined, fallback: number, mustBePositive = false): number {
  if (v === undefined || !Number.isFinite(v)) return fallback
  if (mustBePositive && v <= 0) return fallback
  return v
}

export function createThinkingObserver(
  rawOpts: ThinkingOpts = {},
  onChange?: (active: boolean, now_t: number) => void,
): ThinkingObserver {
  const opts: Required<ThinkingOpts> = {
    max_duration_ms: safeNum(rawOpts.max_duration_ms, DEFAULTS.max_duration_ms, true),
  }

  let active = false
  let start_t = -Infinity

  function emit(prev: boolean, next: boolean, now_t: number): void {
    if (prev === next || !onChange) return
    try {
      onChange(next, now_t)
    } catch {
      /* swallow */
    }
  }

  function notifyStart(now_t: number): void {
    if (!Number.isFinite(now_t)) return
    const prev = active
    active = true
    start_t = now_t
    emit(prev, active, now_t)
  }

  function exit(now_t: number): void {
    if (!Number.isFinite(now_t)) return
    if (!active) return
    const prev = active
    active = false
    start_t = -Infinity
    emit(prev, active, now_t)
  }

  function notifyFirstChunk(now_t: number): void {
    exit(now_t)
  }

  function notifyEnd(now_t: number): void {
    exit(now_t)
  }

  function tick(now_t: number): boolean {
    if (!Number.isFinite(now_t)) return active
    if (active && Number.isFinite(start_t) && now_t - start_t >= opts.max_duration_ms) {
      // Safety cutoff: force exit.
      const prev = active
      active = false
      start_t = -Infinity
      emit(prev, active, now_t)
    }
    return active
  }

  function isActive(now_t: number): boolean {
    return tick(now_t)
  }

  function debug(): { active: boolean; start_t: number } {
    return { active, start_t }
  }

  return { notifyStart, notifyFirstChunk, notifyEnd, tick, isActive, debug }
}
