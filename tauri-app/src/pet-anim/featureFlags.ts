/**
 * featureFlags.ts — TDD §2.9 (v2: all=off hard kill).
 *
 * Reads localStorage flags so the user can disable any sub-feature of
 * the pet-anim v1 layer without rebuilding. The `all` flag is a hard
 * kill — when off, every per-FR flag forced to false (even if individually
 * 'on') so "known good" baseline is one toggle away.
 *
 * Defensive: every storage read is wrapped in try/catch because Safari
 * private mode and sandboxed iframes can throw on access. On error we
 * default to ON (best-effort enablement), which matches the production
 * desire to "ship visible improvements unless something is wrong".
 */
export type AnimFlag =
  | 'all'
  | 'perlin'
  | 'blink'
  | 'saccade'
  | 'gaze'
  | 'motionpool'
  | 'pointer'

export const FLAG_KEYS: Record<AnimFlag, string> = {
  all: 'deskpet_animation_v1',
  perlin: 'deskpet_anim_perlin',
  blink: 'deskpet_anim_blink',
  saccade: 'deskpet_anim_saccade',
  gaze: 'deskpet_anim_gaze',
  motionpool: 'deskpet_anim_motionpool',
  pointer: 'deskpet_anim_pointer',
}

function readFlag(storage: Storage | undefined, key: string): string | null {
  if (!storage) return null
  try {
    return storage.getItem(key)
  } catch {
    return null
  }
}

export function isEnabled(flag: AnimFlag, storage?: Storage): boolean {
  const s = storage ?? (typeof localStorage !== 'undefined' ? localStorage : undefined)
  // Hard kill: if all === 'off', every flag is forced false.
  const all = readFlag(s, FLAG_KEYS.all)
  if (all === 'off') return false
  if (flag === 'all') {
    // null/'' → default on
    return all !== 'off'
  }
  const v = readFlag(s, FLAG_KEYS[flag])
  if (v === 'off') return false
  // null / 'on' / anything else → on
  return true
}
