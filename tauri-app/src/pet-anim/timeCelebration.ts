/**
 * timeCelebration (pet-anim/C3) — Pet Animation UX v2.
 *
 * Time-based celebration triggers per PRD §3 C3:
 *   - Hourly: every wall-clock minute == 0 (DND-suppressed via M-9)
 *   - Anniversary: every MM-DD match in `anniversaries` (DND does NOT suppress)
 *
 * Pure FSM with injected `clock` (defaults to () => new Date()) so unit tests
 * can drive arbitrary timestamps. Uses external `dnd_check` callback so the
 * D-N-D source of truth lives in dndDetector.
 *
 * The caller drives `tick(now_t)` from the 100ms animation-frame interval and
 * receives `onCelebration(kind, message, now_t)` exactly once per minute
 * boundary (debounced via last_fired_minute state).
 *
 * Anniversaries persist in localStorage `deskpet_anniversaries` per PRD §3 C3
 * (configuration UI is OUT of v2 scope per PRD §10 OQ-D2 — users hand-edit
 * the JSON for now).
 */

export type CelebrationKind = 'hourly' | 'anniversary'

export interface Anniversary {
  /** "MM-DD" format. */
  date: string
  message: string
}

export interface TimeCelebrationOpts {
  hourly_enabled?: boolean
  anniversaries?: Anniversary[]
  /** Caller-injected clock for tests. */
  clock?: () => Date
  /** DND check; if returns true, hourly is SKIPPED (anniversary not affected). */
  dnd_check?: () => boolean
  /** Caller-injected storage for anniversaries persistence (defaults to global localStorage). */
  storage?: Storage
}

export interface TimeCelebration {
  tick(now_t: number): void
  /** Add an anniversary at runtime (writes through to storage). */
  addAnniversary(a: Anniversary): void
  /** Force-trigger for tests. */
  fire(kind: CelebrationKind, message: string, now_t: number): void
  /** Snapshot for diagnostics. */
  debug(): {
    last_fired_minute: number
    last_skipped_for_dnd: boolean
    anniversary_count: number
  }
}

const STORAGE_KEY = 'deskpet_anniversaries'

function readAnniversaries(storage?: Storage): Anniversary[] {
  if (!storage) return []
  try {
    const raw = storage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter((a): a is Anniversary =>
      a && typeof a.date === 'string' && /^\d{2}-\d{2}$/.test(a.date) && typeof a.message === 'string',
    )
  } catch {
    return []
  }
}

function writeAnniversaries(storage: Storage, anns: Anniversary[]): void {
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify(anns))
  } catch {
    /* swallow — storage full / private mode */
  }
}

function pad2(n: number): string {
  return n < 10 ? `0${n}` : `${n}`
}

export function createTimeCelebration(
  rawOpts: TimeCelebrationOpts,
  onCelebration: (kind: CelebrationKind, message: string, now_t: number) => void,
): TimeCelebration {
  const opts: Required<Omit<TimeCelebrationOpts, 'storage'>> & { storage?: Storage } = {
    hourly_enabled: rawOpts.hourly_enabled ?? true,
    anniversaries: rawOpts.anniversaries ?? [],
    clock: rawOpts.clock ?? (() => new Date()),
    dnd_check: rawOpts.dnd_check ?? (() => false),
    storage: rawOpts.storage,
  }

  // Merge passed-in + storage anniversaries (in-mem cache).
  const stored = readAnniversaries(opts.storage)
  let anniversaries: Anniversary[] = [...opts.anniversaries, ...stored]

  let last_fired_minute = -1
  let last_skipped_for_dnd = false

  function fireSafe(kind: CelebrationKind, message: string, now_t: number): void {
    try {
      onCelebration(kind, message, now_t)
    } catch {
      /* swallow */
    }
  }

  function tick(now_t: number): void {
    if (!Number.isFinite(now_t)) return
    const date = opts.clock()
    if (!(date instanceof Date) || Number.isNaN(date.getTime())) return
    const minute = date.getMinutes()
    const hour = date.getHours()
    const mmdd = `${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`
    // The "wall minute" for debounce: combine date + minute so we re-trigger
    // properly across hour boundaries (every minute=0 should fire once even
    // if tick runs many times in that minute).
    const wall_minute_id = date.getFullYear() * 100000 + (date.getMonth() + 1) * 1000 + date.getDate() * 100 + hour * 1 // unique per hour-of-day-of-year (anniversary handled separately)
    // Use minute boundary debounce: when minute === 0 AND we haven't fired this hour yet.
    const this_hour_id = wall_minute_id

    if (minute === 0 && this_hour_id !== last_fired_minute) {
      // Hourly trigger candidate.
      last_fired_minute = this_hour_id

      // Anniversary check first (DND does NOT suppress anniversary).
      const anniv = anniversaries.find((a) => a.date === mmdd)
      if (anniv && hour === 0) {
        // Fire at midnight of the anniversary day.
        fireSafe('anniversary', anniv.message, now_t)
        last_skipped_for_dnd = false
        return
      }

      // Hourly (DND suppresses).
      if (opts.hourly_enabled) {
        const dnd = !!opts.dnd_check()
        if (dnd) {
          last_skipped_for_dnd = true
          return
        }
        last_skipped_for_dnd = false
        fireSafe('hourly', `${hour}点啦~`, now_t)
      }
    }
  }

  function addAnniversary(a: Anniversary): void {
    if (!a || typeof a.date !== 'string' || !/^\d{2}-\d{2}$/.test(a.date)) return
    if (typeof a.message !== 'string') return
    anniversaries = [...anniversaries, a]
    if (opts.storage) {
      const persistOnly = anniversaries.filter(
        (x) => !(opts.anniversaries.some((o) => o.date === x.date && o.message === x.message)),
      )
      writeAnniversaries(opts.storage, persistOnly)
    }
  }

  function fire(kind: CelebrationKind, message: string, now_t: number): void {
    fireSafe(kind, message, now_t)
  }

  function debug(): {
    last_fired_minute: number
    last_skipped_for_dnd: boolean
    anniversary_count: number
  } {
    return {
      last_fired_minute,
      last_skipped_for_dnd,
      anniversary_count: anniversaries.length,
    }
  }

  return { tick, addAnniversary, fire, debug }
}
