// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

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
  // v2 flags (PRD §4.1):
  | 'v2_all'
  | 'held'
  | 'user_input'
  | 'thinking'
  | 'viseme'
  | 'mouth_fade'
  | 'low_energy'
  | 'welcome'
  | 'time_celebration'
  | 'emotion'
  | 'milestone'
  | 'edge'
  | 'occlusion'
  | 'dnd'
  | 'dnd_fullscreen'
  | 'dnd_typing'
  | 'dnd_call'

export const FLAG_KEYS: Record<AnimFlag, string> = {
  all: 'deskpet_animation_v1',
  perlin: 'deskpet_anim_perlin',
  blink: 'deskpet_anim_blink',
  saccade: 'deskpet_anim_saccade',
  gaze: 'deskpet_anim_gaze',
  motionpool: 'deskpet_anim_motionpool',
  pointer: 'deskpet_anim_pointer',
  // v2 flags (PRD §4.1):
  v2_all: 'deskpet_animation_v2',
  held: 'deskpet_anim_held',
  user_input: 'deskpet_anim_user_input',
  thinking: 'deskpet_anim_thinking',
  viseme: 'deskpet_anim_viseme',
  mouth_fade: 'deskpet_anim_mouth_fade',
  low_energy: 'deskpet_anim_low_energy',
  welcome: 'deskpet_anim_welcome',
  time_celebration: 'deskpet_anim_time_celebration',
  emotion: 'deskpet_anim_emotion',
  milestone: 'deskpet_anim_milestone',
  edge: 'deskpet_anim_edge',
  occlusion: 'deskpet_anim_occlusion',
  dnd: 'deskpet_anim_dnd',
  dnd_fullscreen: 'deskpet_anim_dnd_fullscreen',
  dnd_typing: 'deskpet_anim_dnd_typing',
  dnd_call: 'deskpet_anim_dnd_call',
}

/**
 * v2 flag set — every v2 FR / sub-flag honours the v2_all hard kill so the
 * AC-3 zero-regression suite can toggle the entire v2 layer off in one move.
 */
const V2_FLAGS = new Set<AnimFlag>([
  'held',
  'user_input',
  'thinking',
  'viseme',
  'mouth_fade',
  'low_energy',
  'welcome',
  'time_celebration',
  'emotion',
  'milestone',
  'edge',
  'occlusion',
  'dnd',
  'dnd_fullscreen',
  'dnd_typing',
  'dnd_call',
])

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

  // Hard kill: v1 master 'all' forces every v1 flag false.
  const all = readFlag(s, FLAG_KEYS.all)
  // v2 hard kill: v2_all forces every v2 flag false (AC-3.3 requirement).
  const v2_all = readFlag(s, FLAG_KEYS.v2_all)

  // Master flags themselves
  if (flag === 'all') return all !== 'off'
  if (flag === 'v2_all') {
    // v2 is a delta on top of v1: v1 'all'=off also kills v2.
    if (all === 'off') return false
    return v2_all !== 'off'
  }

  // v2 sub-flags require v2_all on AND v1 'all' on (v2 is a delta on top of v1).
  if (V2_FLAGS.has(flag)) {
    if (all === 'off') return false
    if (v2_all === 'off') return false
    const v = readFlag(s, FLAG_KEYS[flag])
    // viseme uses 'auto' (default), 'on', or 'off'; only 'off' disables here.
    if (v === 'off') return false
    return true
  }

  // v1 sub-flags require v1 'all' on.
  if (all === 'off') return false
  const v = readFlag(s, FLAG_KEYS[flag])
  if (v === 'off') return false
  return true
}

/** Convenience: returns the viseme flag's literal string ('auto'|'on'|'off'|null). */
export function readVisemeMode(storage?: Storage): 'auto' | 'on' | 'off' {
  const s = storage ?? (typeof localStorage !== 'undefined' ? localStorage : undefined)
  const v = readFlag(s, FLAG_KEYS.viseme)
  if (v === 'off') return 'off'
  if (v === 'on') return 'on'
  return 'auto'
}
