/**
 * Per-test setup for the pet-anim suite (TDD §5.4).
 *
 * Wipes localStorage between tests so feature-flag and motion-label
 * fixtures don't leak across cases. `try/catch` is defensive: in
 * environments where localStorage is disabled (Safari private mode,
 * sandboxed iframes) the access can throw — we never want a setup
 * step to take the whole suite down.
 */
import { beforeEach } from 'vitest'

beforeEach(() => {
  try {
    localStorage.clear()
  } catch {
    /* localStorage unavailable — tests must tolerate this anyway */
  }
})
