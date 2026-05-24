/**
 * Window event stub for Live2DCanvas integration tests (TDD §5.3).
 *
 * Lets a test pretend "window" without touching jsdom's real window
 * object — useful when verifying overlay wiring without React.
 */
export interface WindowStubLike {
  addEventListener: (type: string, fn: (e: unknown) => void) => void
  removeEventListener: (type: string, fn: (e: unknown) => void) => void
  dispatch: (type: string, payload: unknown) => void
  counts: () => Record<string, number>
}

export function makeWindowStub(): WindowStubLike {
  const listeners: Record<string, Array<(e: unknown) => void>> = {}
  return {
    addEventListener: (type, fn) => {
      ;(listeners[type] ||= []).push(fn)
    },
    removeEventListener: (type, fn) => {
      const arr = listeners[type] || []
      const i = arr.indexOf(fn)
      if (i >= 0) arr.splice(i, 1)
    },
    dispatch: (type, payload) => {
      ;(listeners[type] || []).forEach((fn) => fn(payload))
    },
    counts: () =>
      Object.fromEntries(Object.entries(listeners).map(([k, v]) => [k, v.length])),
  }
}
